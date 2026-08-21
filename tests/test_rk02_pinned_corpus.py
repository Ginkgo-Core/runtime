"""What the Domain Router actually does to the real catalog at the pinned commits.

The answer is: **nothing routes.** Not one capability. That is a finding about the
registries, not a defect in the router, and it is asserted here so it cannot drift
unnoticed in either direction — if a record becomes routable, this fails and
somebody looks at why.

Two independent reasons, either of which alone would be sufficient:

1. **No admitted descriptor declares `side_effect_class`.** `RuntimeRouteDecision`
   requires it on a `ROUTED` outcome and the router refuses to default it — a
   side-effect class is a governance statement about what an execution may do.
2. **Every admitted descriptor is `routing_eligible: false`.** Every one sits at
   `FEATURE` or `PROTOTYPE`, and section 3 permits neither to serve caller traffic.

Both are migration backlog for `ginkgo-agents` and `ginkgo-library`.
"""

from __future__ import annotations

import pytest

from ginkgo_runtime.router import EXECUTION_MODES

from .conftest import capability_request, domain_router, routing_context


@pytest.fixture(scope="module")
def pinned_router(request):
    from ginkgo_runtime.catalog.build import build_catalog
    from ginkgo_runtime.catalog.manifest import load_manifest
    from ginkgo_runtime.contracts import load_frozen_contracts

    from .conftest import REPO_ROOT

    contracts = load_frozen_contracts(load_manifest(REPO_ROOT).read)
    return domain_router(build_catalog(REPO_ROOT), contracts)


def capabilities_in(router):
    return sorted(str(record["record_id"]) for record in router.catalog.capabilities)


def test_only_the_reference_descriptor_declares_a_side_effect_class(pinned_router):
    """Reason 1, narrowed to what is still true.

    Not one *upstream* descriptor declares it. RK-09's reference descriptor does,
    which is what lets the slice route at all -- and it is the only record in the
    corpus that can.
    """
    declared = {
        str(record["record_id"]): record["side_effect_class"]
        for record in pinned_router.catalog.skills
    }
    assert declared.pop("rk-reference-echo") == "local_transform"
    assert set(declared.values()) == {None}


def test_no_admitted_descriptor_is_routing_eligible(pinned_router):
    """Reason 2 -- and it holds for the reference descriptor too.

    ``routing_eligible`` stays false until RK-10 emits the certificate. The
    reference skill is subject to exactly the same lifecycle law as a business
    skill; that is the point of it.
    """
    for record in pinned_router.catalog.skills:
        assert record["routing"]["routing_eligible"] is False
        assert record["routing"]["production_eligible"] is False
        assert record["lifecycle_status"] in {"FEATURE", "PROTOTYPE"}


@pytest.mark.parametrize("mode", EXECUTION_MODES)
def test_nothing_in_the_pinned_catalog_routes_in_any_mode(pinned_router, mode):
    """Including ``certification``, which consults neither eligibility flag.

    Certification gets past the ladder and then still fails, on the missing
    ``side_effect_class``. That is the two reasons being genuinely independent.
    """
    routed = []
    for capability_id in capabilities_in(pinned_router):
        decision = pinned_router.route(
            capability_request(
                capability={"name": capability_id, "contract_version": "v1"},
                idempotency_key="idem-%s" % capability_id,
            ),
            routing_context(
                execution_mode=mode, certification_authorization_ref="cert-auth-0001"
            ),
        )
        if decision["resolved"]["outcome"] == "ROUTED":
            routed.append(capability_id)

    assert routed == []


def test_the_reason_a_feature_binding_cannot_route_names_its_lifecycle(pinned_router):
    """And names the *matrix cell*, not the eligibility flag.

    The router used to attribute this block to ``routing_eligible: false``. RK-04
    is more precise: the section 3 matrix marks ``FEATURE`` as "no" in
    ``non_production`` outright, so the record would be denied even if it declared
    the flag true. Blaming the flag suggested a fix that would not work.
    """
    decision = pinned_router.route(
        capability_request(
            capability={"name": "commerce.catalog.search", "contract_version": "v1"}
        ),
        routing_context(execution_mode="non_production"),
    )
    assert decision["resolved"]["outcome"] == "BLOCKED_LIFECYCLE"
    assert decision["routing_reason"].startswith("LIFECYCLE_MODE_DENIED:")
    assert "FEATURE" in decision["routing_reason"]
    assert "non_production" in decision["routing_reason"]


def test_certification_reaches_the_pin_guard_and_stops_there(pinned_router):
    """The lifecycle no longer blocks, so the *next* honest refusal surfaces."""
    decision = pinned_router.route(
        capability_request(
            capability={"name": "commerce.catalog.search", "contract_version": "v1"}
        ),
        routing_context(
            execution_mode="certification", certification_authorization_ref="cert-auth-0001"
        ),
    )
    assert decision["resolved"]["outcome"] == "NO_ELIGIBLE_ROUTE"
    assert "side_effect_class" in decision["routing_reason"]


def test_a_capability_with_two_bindings_is_ambiguous_rather_than_ranked(pinned_router):
    """``commerce.orders.read`` declares an Amazon binding and a Shopee one.

    `ginkgo-library`'s `capabilities.yaml` discriminates them with
    ``routing_key: marketplace_ref`` and ``bindings[].when``, which RK-07 does not
    project. Until it does, there is no governed basis for choosing, and the
    router refuses rather than inventing a preference. Never silently crossing
    marketplaces is exactly what the registry's own rules require.
    """
    decision = pinned_router.route(
        capability_request(
            capability={"name": "commerce.orders.read", "contract_version": "v1"}
        ),
        routing_context(
            execution_mode="certification", certification_authorization_ref="cert-auth-0001"
        ),
    )
    assert decision["resolved"]["outcome"] == "NO_ELIGIBLE_ROUTE"
    assert "amazon-spapi.orders.read" in decision["routing_reason"]
    assert "shopee-open-api.orders.read" in decision["routing_reason"]


def test_a_capability_the_catalog_never_heard_of_is_unknown(pinned_router):
    decision = pinned_router.route(
        capability_request(
            capability={"name": "diagnostics.echo.execute", "contract_version": "v1"}
        ),
        routing_context(),
    )
    assert decision["resolved"]["outcome"] == "UNKNOWN_CAPABILITY"


def test_the_only_null_adapter_in_the_corpus_carries_its_reasons(pinned_router):
    """``shopee.product.normalize`` is a deterministic local transform.

    RK-07 did not project the two reasons until RK-02 needed them; this asserts
    they arrive, so the router can emit an honest null instead of refusing.
    """
    binding = pinned_router.catalog.skill("shopee.product.normalize")["implementation_binding"]

    assert binding["adapter_id"] is None
    assert binding["provider"] is None
    assert len(binding["adapter_not_applicable_reason"]) >= 8
    assert len(binding["provider_not_applicable_reason"]) >= 8


def test_every_decision_over_the_pinned_catalog_validates(pinned_router, contracts):
    """Whatever the corpus does, the artifact is contract-conformant."""
    for capability_id in capabilities_in(pinned_router):
        decision = pinned_router.route(
            capability_request(
                capability={"name": capability_id, "contract_version": "v1"},
                idempotency_key="idem-%s" % capability_id,
            ),
            routing_context(),
        )
        assert contracts.runtime_route_decision.is_valid(decision), capability_id
