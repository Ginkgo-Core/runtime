"""The NEVER list — asserted structurally where possible.

`ai-stack#40`'s Done-when asks for confirmation that ``ranked_fallbacks`` and
``executor_ref`` are rejected. These are that confirmation, plus the rest of the
list the issue and the frozen contract name.
"""

from __future__ import annotations

import io
import os
import tokenize

import pytest

from ginkgo_runtime.router import DecisionValidationError, StaticPolicy, validate_decision
from ginkgo_runtime.router.router import EXECUTION_MODES

from .conftest import (
    REPO_ROOT,
    capability_request,
    domain_router,
    routable_catalog,
    routing_context,
)

PACKAGE = os.path.join(REPO_ROOT, "src", "ginkgo_runtime", "router")


def router_sources():
    """Every router module, as executable code only.

    Comments and string literals are stripped, so a module that *documents* why it
    carries no ``executor_ref`` does not fail the test asserting it carries none.
    """
    for name in sorted(os.listdir(PACKAGE)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(PACKAGE, name)
        with open(path, encoding="utf-8") as handle:
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


# -- no executor reference --------------------------------------------------


def test_no_decision_carries_an_executor_reference(routable):
    """Invariant 4: fusing skill and executor selection is the defect this prevents."""
    decision = routable.route(capability_request(), routing_context())
    body = repr(decision)

    for token in ("executor", "executor_id", "executor_ref"):
        assert token not in body


def test_the_router_package_never_mentions_an_executor_in_code():
    offenders = [
        (name, token)
        for name, code in router_sources()
        for token in ("executor_ref", "executor_id", "chosenExecutorRef")
        if token in code
    ]
    assert offenders == []


def test_an_executor_reference_is_rejected_by_the_contract(routable, contracts):
    """Structural: ``RuntimeRouteDecision`` is ``additionalProperties: false``."""
    decision = dict(routable.route(capability_request(), routing_context()))
    decision["executor_ref"] = "n8n-worker-7"

    with pytest.raises(DecisionValidationError):
        validate_decision(decision, contracts)


# -- no candidate list ------------------------------------------------------


def test_no_decision_carries_a_ranked_fallback_list(routable):
    decision = routable.route(capability_request(), routing_context())
    assert set(decision["fallback"]) == {"policy", "used"}
    assert "ranked_fallbacks" not in repr(decision)
    assert "preferred_skill_ids" not in repr(decision)


def test_a_ranked_fallback_list_is_rejected_by_the_contract(routable, contracts):
    decision = dict(routable.route(capability_request(), routing_context()))
    decision["ranked_fallbacks"] = ["a.b.c", "d.e.f"]

    with pytest.raises(DecisionValidationError) as excinfo:
        validate_decision(decision, contracts)
    assert "ranked_fallbacks" in str(excinfo.value)


def test_the_router_package_never_ranks_anything():
    offenders = [
        (name, token)
        for name, code in router_sources()
        for token in (
            "rankedFallbacks",
            "ranked_fallbacks",
            "preferred_skill",
            ".sort(",
            "sorted(candidates",
        )
        if token in code
    ]
    assert offenders == []


def test_the_forbidden_field_list_comes_from_the_contract(contracts):
    """Read from ``x-ginkgo-forbidden-fields``, not restated here."""
    forbidden = contracts.route_decision_forbidden_fields
    assert "executor_ref" in forbidden
    assert "ranked_fallbacks" in forbidden
    assert "preferred_skill_ids" in forbidden


@pytest.mark.parametrize("field", ["credential", "secret", "api_key", "endpoint", "workflow_id"])
def test_every_forbidden_field_is_rejected(routable, contracts, field):
    decision = dict(routable.route(capability_request(), routing_context()))
    decision[field] = "value"
    with pytest.raises(DecisionValidationError):
        validate_decision(decision, contracts)


# -- fallback is a mode, never a candidate ----------------------------------


@pytest.mark.parametrize("policy", ["none", "same_semantics_only"])
def test_both_frozen_fallback_policies_are_carried_through(tmp_path, contracts, policy):
    router = domain_router(routable_catalog(tmp_path), contracts)
    decision = router.route(
        capability_request(constraints={"fallback_policy": policy}), routing_context()
    )
    assert decision["fallback"]["policy"] == policy
    assert decision["fallback"]["used"] is False


def test_same_semantics_only_authorises_a_mode_and_names_nothing(tmp_path, contracts):
    """It means "a same-semantics substitution would be permitted **if one were
    proven**", and proving one requires a ``same_semantics_proof_ref``.

    This router never produces such a proof, so it never sets ``used``. A router
    that ranked candidates and picked the best surviving one would be
    manufacturing the chain P0 froze out.
    """
    router = domain_router(routable_catalog(tmp_path), contracts)
    decision = router.route(
        capability_request(constraints={"fallback_policy": "same_semantics_only"}),
        routing_context(),
    )
    assert decision["fallback"] == {"policy": "same_semantics_only", "used": False}
    assert "same_semantics_proof_ref" not in decision["fallback"]


def test_a_used_fallback_without_a_proof_is_rejected_by_the_contract(routable, contracts):
    """The guard that makes ``used: true`` expensive rather than convenient."""
    decision = dict(routable.route(capability_request(), routing_context()))
    decision["fallback"] = {"policy": "same_semantics_only", "used": True}

    with pytest.raises(DecisionValidationError):
        validate_decision(decision, contracts)


def test_the_router_never_sets_fallback_used(tmp_path, contracts):
    """Across every outcome, not only the happy one."""
    for policy in (StaticPolicy.allowing(), StaticPolicy.allowing(decision="DENY")):
        router = domain_router(routable_catalog(tmp_path), contracts, policy)
        for mode in EXECUTION_MODES:
            decision = router.route(
                capability_request(),
                routing_context(
                    execution_mode=mode, certification_authorization_ref="cert-auth-0001"
                ),
            )
            assert decision["fallback"]["used"] is False


# -- no caller authority, no live registry, no second router ----------------


def test_the_router_reads_no_field_a_caller_could_have_supplied(routable):
    """Invariants 17 and 18.

    RK-01 already strips implementation fields, so belt-and-braces: the router's
    code names none of them either, and could not act on one if it arrived.
    """
    offenders = [
        (name, token)
        for name, code in router_sources()
        for token in (
            'request["provider"',
            'request["adapter_id"',
            'request["skill_id"',
            'request.get("provider"',
            'request.get("skill_id"',
        )
        if token in code
    ]
    assert offenders == []


def test_the_router_opens_no_socket(routable):
    """Never a live registry fetch mid-request."""
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("the Domain Router attempted network I/O")

    real_socket, real_create = socket.socket, socket.create_connection
    socket.socket = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    try:
        decision = routable.route(capability_request(), routing_context())
    finally:
        socket.socket = real_socket  # type: ignore[assignment]
        socket.create_connection = real_create  # type: ignore[assignment]

    assert decision["resolved"]["outcome"] == "ROUTED"


def test_the_router_writes_nothing(routable):
    import builtins

    real_open = builtins.open
    attempted: list[str] = []

    def guarded(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            attempted.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    builtins.open = guarded
    try:
        routable.route(capability_request(), routing_context())
    finally:
        builtins.open = real_open

    assert attempted == []


def test_the_kernel_ships_exactly_one_component_named_router():
    """*"Five different things are already called a router across the ecosystem."*"""
    source_root = os.path.join(REPO_ROOT, "src", "ginkgo_runtime")
    packages = sorted(
        name
        for name in os.listdir(source_root)
        if os.path.isdir(os.path.join(source_root, name)) and not name.startswith("__")
    )
    assert [name for name in packages if name.endswith("router")] == ["router"]


def test_the_router_does_not_depend_on_ginkgo_connect():
    """BLK-17 ``KEEP_SEPARATE``: the hashing model is generalized, the package is not."""
    offenders = [
        (name, token)
        for name, code in router_sources()
        for token in ("@connect/", "ginkgo_connect", "FrozenDecision")
        if token in code
    ]
    assert offenders == []


def test_the_decision_is_never_part_of_a_caller_projection(routable):
    """*"It MUST NOT reach an unprivileged caller projection."*

    RK-01 builds the caller projection from a ``RouteView``, which is a narrow
    slice. The decision itself has no path to a caller.
    """
    from ginkgo_runtime.bus import caller_projection
    from ginkgo_runtime.router import to_route_view

    decision = routable.route(capability_request(), routing_context())
    view = to_route_view(decision)

    assert view.skill_id == "rk-reference-echo"
    assert view.adapter_id is None
    # The seam carries no decision hash, no policy snapshot hash, no pinned refs.
    for absent in ("decision_hash", "decision_inputs_hash", "policy_snapshot_hash", "pinned_refs"):
        assert absent not in repr(view)

    projected = caller_projection(
        {"routing": {"adapter_id": view.adapter_id, "provider": view.provider, "domain": "system"}}
    )
    assert "adapter_id" not in projected["routing"]
