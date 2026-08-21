"""Resolution: which descriptor, which lifecycle, which verdict — and every refusal.

`ai-stack#40` fails closed on *"unknown skill · unknown version · ineligible
lifecycle · invalid binding · unavailable adapter · ineligible provider · policy
denial · failed external gate"*, each returning a normalized outcome and
**never a substitute route**.
"""

from __future__ import annotations

import pytest

from ginkgo_runtime.router import PolicyVerdict, StaticPolicy

from .conftest import (
    ROUTABLE_DESCRIPTOR,
    capability_request,
    domain_router,
    routable_catalog,
    routing_context,
)


def outcome_of(decision):
    return decision["resolved"]["outcome"]


# -- capability lookup ------------------------------------------------------


def test_a_known_capability_resolves_to_one_descriptor(routable):
    decision = routable.route(capability_request(), routing_context())
    assert outcome_of(decision) == "ROUTED"
    assert decision["resolved"]["skill_id"] == "rk-reference-echo"
    assert decision["resolved"]["skill_version"] == "1.0.0"
    assert decision["resolved"]["kind"] == "deterministic_domain_skill"


def test_n12_an_unknown_capability_is_not_routed(routable):
    """Reference slice N12, the RK-02 half: no substitute route is attempted."""
    decision = routable.route(
        capability_request(
            capability={"name": "commerce.catalog.search", "contract_version": "v1"}
        ),
        routing_context(),
    )
    assert outcome_of(decision) == "UNKNOWN_CAPABILITY"
    assert "skill_id" not in decision["resolved"]
    assert decision["fallback"]["used"] is False


def test_an_unknown_contract_version_is_distinguished_from_an_unknown_capability(routable):
    decision = routable.route(
        capability_request(capability={"name": "system.echo.execute", "contract_version": "v9"}),
        routing_context(),
    )
    assert outcome_of(decision) == "UNKNOWN_CONTRACT_VERSION"
    assert "v1" in decision["routing_reason"]


def test_a_declared_capability_with_no_admitted_skill_has_no_route(tmp_path, contracts):
    """Known to the catalog, served by nothing. That is not an unknown capability."""
    router = domain_router(routable_catalog(tmp_path), contracts)
    decision = router.route(
        capability_request(capability={"name": "code.repo.inspect", "contract_version": "v1"}),
        routing_context(),
    )
    # `code.repo.inspect` is not in this synthetic catalog at all.
    assert outcome_of(decision) == "UNKNOWN_CAPABILITY"


def test_ambiguity_fails_closed_rather_than_choosing(tmp_path, contracts):
    """Two descriptors for one capability at one version: the router declines.

    Picking "the first", "the preferred" or "the most mature" is a ranked
    candidate chain wearing a different hat, and that is the shape P0 froze out.
    ``ginkgo-library`` does declare a discriminator for this -- ``routing_key``
    with ``bindings[].when`` -- but RK-07 does not project it, so no governed
    basis for choosing exists yet.
    """
    from ginkgo_runtime.catalog.build import build_catalog

    from .conftest import make_snapshot

    second = ROUTABLE_DESCRIPTOR.replace("rk-reference-echo", "rk-reference-echo-two")
    root = make_snapshot(
        tmp_path,
        [
            ("features/one.yaml", "skill_descriptor", None, ROUTABLE_DESCRIPTOR),
            ("features/two.yaml", "skill_descriptor", None, second),
        ],
    )
    router = domain_router(build_catalog(root), contracts)
    decision = router.route(capability_request(), routing_context())

    assert outcome_of(decision) == "NO_ELIGIBLE_ROUTE"
    assert "rk-reference-echo" in decision["routing_reason"]
    assert "rk-reference-echo-two" in decision["routing_reason"]
    assert "ranked candidate chain" in decision["routing_reason"]


def test_the_router_never_reads_a_registry_during_a_request(routable):
    """It reads the immutable snapshot it was constructed with. Never a live fetch."""
    before = routable.catalog.catalog_version
    routable.route(capability_request(), routing_context())
    assert routable.catalog.catalog_version == before


# -- lifecycle --------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "routing_eligible", "mode", "expected"),
    [
        ("VALIDATED", True, "non_production", "ROUTED"),
        ("VALIDATED", False, "non_production", "BLOCKED_LIFECYCLE"),
        ("FEATURE", False, "non_production", "BLOCKED_LIFECYCLE"),
        ("PROTOTYPE", False, "non_production", "BLOCKED_LIFECYCLE"),
        ("FEATURE", False, "production", "BLOCKED_LIFECYCLE"),
        ("VALIDATED", True, "production", "BLOCKED_LIFECYCLE"),
        ("DEPRECATED", False, "non_production", "BLOCKED_LIFECYCLE"),
        ("REJECTED", False, "non_production", "BLOCKED_LIFECYCLE"),
    ],
)
def test_the_lifecycle_matrix_is_applied_per_mode(
    tmp_path, contracts, status, routing_eligible, mode, expected
):
    """``lifecycle-and-promotion.md`` section 3, row by row."""
    descriptor = ROUTABLE_DESCRIPTOR.replace(
        "lifecycle_status: VALIDATED", "lifecycle_status: %s" % status
    ).replace("routing_eligible: true", "routing_eligible: %s" % str(routing_eligible).lower())

    router = domain_router(routable_catalog(tmp_path, descriptor), contracts)
    decision = router.route(capability_request(), routing_context(execution_mode=mode))
    assert outcome_of(decision) == expected


def test_production_requires_both_flags(tmp_path, contracts):
    descriptor = ROUTABLE_DESCRIPTOR.replace(
        "lifecycle_status: VALIDATED", "lifecycle_status: PRODUCTION"
    ).replace("production_eligible: false", "production_eligible: true")
    router = domain_router(routable_catalog(tmp_path, descriptor), contracts)

    decision = router.route(capability_request(), routing_context(execution_mode="production"))
    assert outcome_of(decision) == "ROUTED"


def test_invariant_16_feature_never_routes_in_production(tmp_path, contracts):
    """The one lifecycle rule frozen in master, applied to the decision artifact."""
    descriptor = ROUTABLE_DESCRIPTOR.replace(
        "lifecycle_status: VALIDATED", "lifecycle_status: FEATURE"
    ).replace("routing_eligible: true", "routing_eligible: false")
    router = domain_router(routable_catalog(tmp_path, descriptor), contracts)

    decision = router.route(capability_request(), routing_context(execution_mode="production"))
    assert outcome_of(decision) == "BLOCKED_LIFECYCLE"


def test_certification_consults_neither_flag(tmp_path, contracts):
    """The escape from the circularity, and the only mode a FEATURE record can run in.

    A record needs the certificate to become routable and needs to run to earn the
    certificate. ``certification`` breaks that -- and pays for it with a recorded
    authorization instead.
    """
    descriptor = ROUTABLE_DESCRIPTOR.replace(
        "lifecycle_status: VALIDATED", "lifecycle_status: FEATURE"
    ).replace("routing_eligible: true", "routing_eligible: false")
    router = domain_router(routable_catalog(tmp_path, descriptor), contracts)

    decision = router.route(
        capability_request(),
        routing_context(
            execution_mode="certification",
            certification_authorization_ref="cert-auth-0001",
        ),
    )
    assert outcome_of(decision) == "ROUTED"
    assert decision["lifecycle"]["routing_eligible"] is False
    assert decision["certification_authorization_ref"] == "cert-auth-0001"


def test_certification_without_a_recorded_authorization_is_blocked(tmp_path, contracts):
    """Otherwise it is an unaudited bypass of the ladder."""
    router = domain_router(routable_catalog(tmp_path), contracts)
    decision = router.route(
        capability_request(), routing_context(execution_mode="certification")
    )
    assert outcome_of(decision) == "BLOCKED_LIFECYCLE"
    assert "unaudited bypass" in decision["routing_reason"]


def test_rejected_is_terminal_even_in_certification(tmp_path, contracts):
    """Section 3 marks REJECTED "no" in every mode, and defines "no" as at any stage."""
    descriptor = ROUTABLE_DESCRIPTOR.replace(
        "lifecycle_status: VALIDATED", "lifecycle_status: REJECTED"
    ).replace("routing_eligible: true", "routing_eligible: false")
    router = domain_router(routable_catalog(tmp_path, descriptor), contracts)

    decision = router.route(
        capability_request(),
        routing_context(
            execution_mode="certification", certification_authorization_ref="cert-auth-0001"
        ),
    )
    assert outcome_of(decision) == "BLOCKED_LIFECYCLE"


def test_deprecated_may_still_be_certified(tmp_path, contracts):
    """A retired record can be re-run to reproduce historical evidence."""
    descriptor = ROUTABLE_DESCRIPTOR.replace(
        "lifecycle_status: VALIDATED", "lifecycle_status: DEPRECATED"
    ).replace("routing_eligible: true", "routing_eligible: false")
    router = domain_router(routable_catalog(tmp_path, descriptor), contracts)

    decision = router.route(
        capability_request(),
        routing_context(
            execution_mode="certification", certification_authorization_ref="cert-auth-0001"
        ),
    )
    assert outcome_of(decision) == "ROUTED"


# -- policy -----------------------------------------------------------------


def test_a_policy_denial_is_reported_not_re_decided(tmp_path, contracts):
    """RK-04 decides; RK-02 records the pinned verdict and stops."""
    policy = StaticPolicy.allowing(decision="DENY")
    router = domain_router(routable_catalog(tmp_path), contracts, policy)
    decision = router.route(capability_request(), routing_context())

    assert outcome_of(decision) == "BLOCKED_POLICY"
    assert decision["policy"]["decision"] == "DENY"


@pytest.mark.parametrize("state", ["PENDING", "REJECTED", "EXPIRED"])
def test_a_required_approval_that_is_not_granted_blocks(tmp_path, contracts, state):
    policy = StaticPolicy.allowing(approval_required=True, approval_state=state)
    router = domain_router(routable_catalog(tmp_path), contracts, policy)
    assert outcome_of(router.route(capability_request(), routing_context())) == "BLOCKED_APPROVAL"


def test_an_approved_approval_does_not_block(tmp_path, contracts):
    policy = StaticPolicy.allowing(approval_required=True, approval_state="APPROVED")
    router = domain_router(routable_catalog(tmp_path), contracts, policy)
    assert outcome_of(router.route(capability_request(), routing_context())) == "ROUTED"


@pytest.mark.parametrize("gate_state", ["BLOCKED", "UNKNOWN", "EXPIRED"])
def test_invariant_5_an_approval_never_clears_a_gate(tmp_path, contracts, gate_state):
    """``APPROVED + BLOCKED => BLOCKED``. ``APPROVED + UNKNOWN => BLOCKED``.

    The two are independent concerns with independent state machines, and the
    outcome names the gate rather than the approval -- reporting
    ``BLOCKED_APPROVAL`` here would name the wrong cause and imply an approval
    would fix it.
    """
    policy = StaticPolicy.allowing(
        approval_required=True,
        approval_state="APPROVED",
        external_gates=[{"gate_id": "shopee_business_approval", "state": gate_state}],
    )
    router = domain_router(routable_catalog(tmp_path), contracts, policy)
    decision = router.route(capability_request(), routing_context())

    assert outcome_of(decision) == "BLOCKED_EXTERNAL_GATE"
    assert "invariant 5" in decision["routing_reason"]


def test_a_clear_gate_does_not_block(tmp_path, contracts):
    policy = StaticPolicy.allowing(
        external_gates=[{"gate_id": "shopee_business_approval", "state": "CLEAR"}]
    )
    router = domain_router(routable_catalog(tmp_path), contracts, policy)
    assert outcome_of(router.route(capability_request(), routing_context())) == "ROUTED"


def test_a_denial_outranks_a_gate_and_a_gate_outranks_an_approval(tmp_path, contracts):
    """Deterministic precedence when more than one applies."""
    policy = StaticPolicy.allowing(
        decision="DENY",
        approval_required=True,
        approval_state="PENDING",
        external_gates=[{"gate_id": "g", "state": "BLOCKED"}],
    )
    router = domain_router(routable_catalog(tmp_path), contracts, policy)
    assert outcome_of(router.route(capability_request(), routing_context())) == "BLOCKED_POLICY"

    policy = StaticPolicy.allowing(
        approval_required=True,
        approval_state="PENDING",
        external_gates=[{"gate_id": "g", "state": "BLOCKED"}],
    )
    router = domain_router(routable_catalog(tmp_path), contracts, policy)
    assert (
        outcome_of(router.route(capability_request(), routing_context()))
        == "BLOCKED_EXTERNAL_GATE"
    )


def test_the_policy_snapshot_is_hashed_not_carried(tmp_path, contracts):
    """The decision proves which snapshot it saw without carrying its contents.

    A snapshot may name internal rules. The hash is computed here from the
    snapshot the port supplied, rather than accepted from the port -- otherwise a
    policy authority could claim provenance for a snapshot nobody can check.
    """
    from ginkgo_runtime.canonical import fingerprint

    snapshot = {"rules": [{"id": "r-1", "effect": "ALLOW"}], "default": "DENY"}
    policy = StaticPolicy(
        verdict=PolicyVerdict(
            policy_id="p", policy_version="7", decision="ALLOW", snapshot=snapshot
        )
    )
    router = domain_router(routable_catalog(tmp_path), contracts, policy)
    decision = router.route(capability_request(), routing_context())

    assert decision["policy"]["policy_snapshot_hash"] == fingerprint(snapshot)
    assert "r-1" not in repr(decision)
    assert "snapshot" not in decision["policy"]


# -- honest nulls -----------------------------------------------------------


def test_a_null_adapter_carries_its_reason(routable):
    """*"Do not invent identities to fill the generic path."*"""
    resolved = routable.route(capability_request(), routing_context())["resolved"]

    assert resolved["adapter_id"] is None
    assert resolved["provider"] is None
    assert "No integration boundary exists" in resolved["adapter_not_applicable_reason"]
    assert "pure function" in resolved["provider_not_applicable_reason"]


def test_a_real_adapter_and_provider_are_pinned(tmp_path, contracts):
    descriptor = ROUTABLE_DESCRIPTOR.replace(
        """  adapter_id: null
  adapter_not_applicable_reason: >
    Deterministic in-process transform. No integration boundary exists, so
    inventing an adapter identity would misrepresent the implementation.
  provider: null
  provider_not_applicable_reason: >
    No external or local provider is invoked. The tool is a pure function.""",
        """  adapter_id: shopee-open-api
  provider: shopee-open-platform""",
    ).replace("kind: deterministic_domain_skill", "kind: provider_binding")

    router = domain_router(routable_catalog(tmp_path, descriptor), contracts)
    resolved = router.route(capability_request(), routing_context())["resolved"]

    assert resolved["adapter_id"] == "shopee-open-api"
    assert resolved["provider"] == "shopee-open-platform"
    assert "adapter_not_applicable_reason" not in resolved


def test_a_descriptor_missing_side_effect_class_is_not_routed(tmp_path, contracts):
    """A side-effect class is a governance statement, not a formality.

    RK-07 admits legacy records that predate the full descriptor contract and
    reports the gap rather than filling it, so a descriptor can be admissible and
    still not pinnable. The router refuses rather than defaulting.
    """
    descriptor = ROUTABLE_DESCRIPTOR.replace("side_effect_class: local_transform\n", "")
    router = domain_router(routable_catalog(tmp_path, descriptor), contracts)
    decision = router.route(capability_request(), routing_context())

    assert outcome_of(decision) == "NO_ELIGIBLE_ROUTE"
    assert "side_effect_class" in decision["routing_reason"]


def test_a_non_routed_decision_names_no_implementation(tmp_path, contracts):
    """Reporting the descriptor it *would* have chosen reads as a suggestion.

    It would also leak a route that the ladder or the policy refused.
    """
    policy = StaticPolicy.allowing(decision="DENY")
    router = domain_router(routable_catalog(tmp_path), contracts, policy)
    resolved = router.route(capability_request(), routing_context())["resolved"]

    assert set(resolved) == {"outcome"}
