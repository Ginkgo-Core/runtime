"""Adversarial-review regressions.

Six defects, found by attacking the router rather than by reading it. Four were
reachable from an ordinary, contract-valid request — the router crashed on input
the frozen schema explicitly permits.

They share one root cause worth naming: RK-07's canonical encoder **refuses
floats**, correctly, because a float in a catalog projection is a defect. RK-02
handles caller-derived numbers, where a float is a price. Reusing the stricter
primitive across that boundary is the mistake, and it appeared in three separate
places before it was found.
"""

from __future__ import annotations

import pytest

from ginkgo_runtime.canonical import MAX_DEPTH, CanonicalisationError, canonical_json, fingerprint
from ginkgo_runtime.router import (
    DomainRouter,
    PolicyContractError,
    PolicyVerdict,
    StaticPolicy,
)

from .conftest import (
    ROUTABLE_DESCRIPTOR,
    capability_request,
    domain_router,
    make_snapshot,
    routable_catalog,
    routing_context,
)

SECRET = "AKIAIOSFODNN7EXAMPLE-super-secret"


def deep_document(levels=3000):
    root: dict = {}
    cursor = root
    for _ in range(levels):
        cursor["n"] = {}
        cursor = cursor["n"]
    return root


# -- floats reach the router through legal requests -------------------------


def test_a_fractional_max_cost_does_not_crash_the_router(routable):
    """``max_cost.amount`` is declared ``number`` in the frozen contract.

    ``{"amount": 19.99, "currency": "BRL"}`` validates against
    ``CapabilityRequest v1``, so RK-01 admits it and hands it straight to RK-02.
    Hashing it with the catalog's float-refusing encoder crashed the router on an
    entirely ordinary constraint.
    """
    decision = routable.route(
        capability_request(
            constraints={
                "fallback_policy": "none",
                "max_cost": {"amount": 19.99, "currency": "BRL"},
            }
        ),
        routing_context(),
    )
    assert decision["resolved"]["outcome"] == "ROUTED"
    assert decision["constraints_applied"]["max_cost"]["amount"] == 19.99
    assert len(decision["decision_hash"]) == 64


def test_a_fractional_max_cost_still_participates_in_the_hash(routable):
    cheap = routable.route(
        capability_request(
            constraints={
                "fallback_policy": "none",
                "max_cost": {"amount": 19.99, "currency": "BRL"},
            }
        ),
        routing_context(),
    )
    dear = routable.route(
        capability_request(
            constraints={
                "fallback_policy": "none",
                "max_cost": {"amount": 19.98, "currency": "BRL"},
            }
        ),
        routing_context(),
    )
    assert cheap["decision_hash"] != dear["decision_hash"]


def test_a_policy_snapshot_with_numeric_thresholds_does_not_crash(tmp_path, contracts):
    """Real policies carry them.

    ``commerce-evaluation-gates-v1`` in ``ginkgo-library`` declares
    ``minimum_multiplier: 5.0``. A router that refused floats in a policy snapshot
    could not hash the first real policy it was handed.
    """
    snapshot = {"minimum_multiplier": 5.0, "minimum_operating_margin_pct": 60}
    router = domain_router(
        routable_catalog(tmp_path), contracts, StaticPolicy.allowing(snapshot=snapshot)
    )
    decision = router.route(capability_request(), routing_context())

    assert decision["resolved"]["outcome"] == "ROUTED"
    assert decision["policy"]["policy_snapshot_hash"] == fingerprint(snapshot)


def test_a_business_float_in_input_does_not_crash(routable):
    decision = routable.route(
        capability_request(input={"price": 19.99, "quantity": 3}), routing_context()
    )
    assert decision["resolved"]["outcome"] == "ROUTED"


# -- unbounded nesting ------------------------------------------------------


@pytest.mark.parametrize("encoder", [fingerprint, canonical_json])
def test_both_encoders_refuse_deep_nesting_by_type(encoder):
    """A typed refusal, not a ``RecursionError`` out of whichever component held it."""
    with pytest.raises(CanonicalisationError) as excinfo:
        encoder(deep_document())
    assert str(MAX_DEPTH) in str(excinfo.value)


def test_ordinary_nesting_is_untouched():
    payload = {"order": {"lines": [{"item": {"attributes": {"colour": "red"}}}]}}
    assert len(fingerprint(payload)) == 64
    assert canonical_json(payload)


def test_a_deeply_nested_input_is_refused_by_type_not_by_stack_overflow(routable):
    """RK-01 rejects this long before RK-02 sees it; this is the backstop.

    The router is separately callable, so a host that skipped the Bus could hand
    it a document RK-01 would have refused. It fails with a typed error naming
    the limit rather than an untyped stack overflow.
    """
    with pytest.raises(CanonicalisationError):
        routable.route(capability_request(input=deep_document()), routing_context())


# -- the policy port is foreign code ----------------------------------------


def test_a_policy_port_that_raises_becomes_a_typed_contract_error(tmp_path, contracts):
    class Exploding:
        def evaluate(self, question):
            raise RuntimeError("boom")

    router = DomainRouter(
        catalog=routable_catalog(tmp_path), policy=Exploding(), contracts=contracts
    )
    with pytest.raises(PolicyContractError) as excinfo:
        router.route(capability_request(), routing_context())
    assert "RuntimeError" in str(excinfo.value)


def test_a_policy_port_exception_message_never_escapes(tmp_path, contracts):
    """Policy stores live behind connection strings, and exceptions quote them."""

    class Leaking:
        def evaluate(self, question):
            raise RuntimeError("policy store postgres://user:hunter2@db/prod unreachable")

    router = DomainRouter(
        catalog=routable_catalog(tmp_path), policy=Leaking(), contracts=contracts
    )
    with pytest.raises(PolicyContractError) as excinfo:
        router.route(capability_request(), routing_context())

    body = str(excinfo.value)
    assert "hunter2" not in body
    assert "postgres" not in body


@pytest.mark.parametrize(
    "verdict",
    [
        {"decision": "MAYBE"},
        {"decision": "ALLOW", "approval_state": "PROBABLY"},
    ],
)
def test_an_out_of_enum_verdict_is_refused_rather_than_recorded(tmp_path, contracts, verdict):
    """It is not a denial, and recording one would fabricate a governance verdict.

    ``BLOCKED_POLICY`` in the audit record means a policy authority denied the
    request. A port that answered outside the frozen enum denied nothing.
    """
    fields = {"policy_id": "p", "policy_version": "1", "decision": "ALLOW"}
    fields.update(verdict)
    router = domain_router(
        routable_catalog(tmp_path), contracts, StaticPolicy(verdict=PolicyVerdict(**fields))
    )
    with pytest.raises(PolicyContractError):
        router.route(capability_request(), routing_context())


def test_an_out_of_enum_gate_state_is_refused(tmp_path, contracts):
    policy = StaticPolicy.allowing(external_gates=[{"gate_id": "g", "state": "PROBABLY"}])
    router = domain_router(routable_catalog(tmp_path), contracts, policy)
    with pytest.raises(PolicyContractError):
        router.route(capability_request(), routing_context())


def test_a_refused_verdict_never_leaks_the_snapshot(tmp_path, contracts):
    policy = StaticPolicy(
        verdict=PolicyVerdict(
            policy_id="p", policy_version="1", decision="MAYBE", snapshot={"api_key": SECRET}
        )
    )
    router = domain_router(routable_catalog(tmp_path), contracts, policy)
    with pytest.raises(PolicyContractError) as excinfo:
        router.route(capability_request(), routing_context())
    assert SECRET not in str(excinfo.value)


# -- an oversized routing reason --------------------------------------------


def test_a_long_ambiguity_reason_is_clamped_not_fatal(tmp_path, contracts):
    """The reason names every claimant, and the contract caps it at 4096.

    Before the clamp the router failed its own artifact rather than explaining
    why it refused -- so the more ambiguous the capability, the less it could say
    about it.
    """
    from ginkgo_runtime.catalog.build import build_catalog

    sources = [
        (
            "features/r%03d.yaml" % index,
            "skill_descriptor",
            None,
            ROUTABLE_DESCRIPTOR.replace("rk-reference-echo", "rk-reference-echo-%03d" % index),
        )
        for index in range(200)
    ]
    router = domain_router(build_catalog(make_snapshot(tmp_path, sources)), contracts)
    decision = router.route(capability_request(), routing_context())

    assert decision["resolved"]["outcome"] == "NO_ELIGIBLE_ROUTE"
    assert len(decision["routing_reason"]) <= contracts.routing_reason_max_length
    assert decision["routing_reason"].endswith("... (truncated)")
    assert contracts.runtime_route_decision.is_valid(decision)


def test_the_reason_limit_is_read_from_the_contract(contracts):
    assert contracts.routing_reason_max_length == 4096


def test_a_short_reason_is_not_truncated(routable):
    decision = routable.route(capability_request(), routing_context())
    assert "truncated" not in decision["routing_reason"]
