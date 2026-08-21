"""Inbound admission -- reference slice negatives N1-N8 and N13, and positive P8.

``reference-slice.md`` section 5: *"The slice is not done when the happy path
passes. It is done when the negatives pass."* These are the RK-01 half of that
matrix, and each test names the row it discharges.
"""

from __future__ import annotations

import pytest

from ginkgo_runtime.bus import FORBIDDEN_FIELD_PREFIXES, forbidden_fields_in

from .conftest import capability_request

ACTOR_TYPES = ("HUMAN", "WHATSAPP", "N8N", "HERMES", "OPENCLAW", "SYLVIA", "API", "MCP", "SERVICE")


def code_of(outcome):
    return None if outcome.error is None else outcome.error["code"]


def test_the_baseline_request_is_admitted(bus):
    outcome = bus.submit(capability_request())
    assert outcome.accepted
    assert outcome.status == "SUCCESS"


# -- N1: the capability-name pattern ---------------------------------------


@pytest.mark.parametrize(
    "name",
    ["system.echo", "web.search", "engineering.plan", "commerce", "System.Echo.Execute"],
)
def test_n1_a_sub_three_segment_capability_is_rejected(bus, name):
    """N1. The pattern is loaded from the frozen schema, never restated here."""
    outcome = bus.submit(capability_request(capability={"name": name, "contract_version": "v1"}))
    assert not outcome.accepted
    assert code_of(outcome) == "REQUEST_SCHEMA_VIOLATION"
    assert outcome.error["category"] == "VALIDATION"


def test_the_capability_pattern_comes_from_the_frozen_schema(contracts):
    """Not a literal in this package: the value is read off the pinned contract."""
    assert contracts.capability_name_pattern == r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*){2,}$"


# -- N2 / N3 / N4: caller-agnosticism, by prefix ---------------------------


@pytest.mark.parametrize(
    "name",
    [
        "provider", "provider_id", "adapter", "adapter_id", "binding", "binding_id",
        "skill", "skill_id", "skill_version", "tool", "tool_id", "executor",
        "executor_id", "workflow_id", "endpoint",
    ],
)
def test_n2_a_top_level_implementation_field_is_rejected(bus, name):
    """N2. Invariants 17 and 18: a caller never selects or restricts an implementation."""
    outcome = bus.submit(capability_request(**{name: "amazon"}))
    assert not outcome.accepted
    assert outcome.error["category"] == "VALIDATION"


@pytest.mark.parametrize("name", ["provider_hint", "endpoint_url", "commands"])
def test_n3_prefix_matching_catches_what_an_exact_name_check_misses(bus, name):
    """N3, and the reason the rule is stated as a prefix rule.

    ``ai-stack#39``: *"an exact-name check passes provider_hint, endpoint_url and
    commands"* -- and calls that a real gap in an existing validator. These three
    names are the regression test for that gap.
    """
    outcome = bus.submit(capability_request(input={"message": "hi", name: "amazon"}))
    assert not outcome.accepted
    assert code_of(outcome) == "CALLER_SELECTED_IMPLEMENTATION"
    assert outcome.error["details"]["matching"] == "prefix"


@pytest.mark.parametrize(
    "name",
    ["credential", "api_key", "access_token", "refresh_token", "cookie", "shell_command", "script"],
)
def test_n4_credential_and_command_surfaces_are_rejected(bus, name):
    """N4."""
    outcome = bus.submit(capability_request(input={"message": "hi", name: "value"}))
    assert not outcome.accepted
    assert code_of(outcome) == "CALLER_SELECTED_IMPLEMENTATION"


def test_the_forbidden_scan_reaches_inside_input(bus):
    """The gate that matters. ``input`` is the only open field in the contract.

    The frozen request is ``additionalProperties: false`` at the top, so a
    top-level ``provider`` never survives schema validation. ``input`` is declared
    ``additionalProperties: true`` -- business payload has to be -- so a scan that
    only looked at the top level would admit ``input.provider_hint`` untouched.
    """
    outcome = bus.submit(
        capability_request(input={"order": {"lines": [{"adapter_id": "shopee-open-api"}]}})
    )
    assert code_of(outcome) == "CALLER_SELECTED_IMPLEMENTATION"
    pointers = [f["pointer"] for f in outcome.error["details"]["fields"]]
    assert pointers == ["/input/order/lines/0/adapter_id"]


@pytest.mark.parametrize("name", ["providerHint", "Provider-Id", "SKILL_ID", "Endpoint"])
def test_the_forbidden_scan_is_case_insensitive(bus, name):
    """Otherwise the rule is bypassed by pressing shift."""
    outcome = bus.submit(capability_request(input={name: "x"}))
    assert code_of(outcome) == "CALLER_SELECTED_IMPLEMENTATION"


def test_the_forbidden_scan_reads_names_and_not_values(bus):
    """``input.note = "ask the provider"`` is business prose, not an implementation choice.

    Scanning values would reject ordinary text while catching nothing: the field
    *name* is what would carry implementation authority.
    """
    outcome = bus.submit(
        capability_request(input={"note": "ask the provider for adapter_id and a credential"})
    )
    assert outcome.accepted


def test_no_field_of_the_frozen_request_trips_the_forbidden_scan():
    """A false positive here would make the contract unusable, so it is pinned."""
    assert forbidden_fields_in(capability_request()) == []


def test_every_prefix_is_the_shortest_of_its_family():
    """No prefix in the list is made redundant by a shorter one beside it.

    A redundant entry is harmless but hides whether the shorter one is doing the
    work, and the list is the machine-readable form of invariant 17.
    """
    redundant = [
        longer
        for longer in FORBIDDEN_FIELD_PREFIXES
        for shorter in FORBIDDEN_FIELD_PREFIXES
        if shorter != longer and longer.startswith(shorter)
    ]
    assert redundant == []


# -- N5, N6, N7, N8: the frozen constraint shapes --------------------------


def test_n5_a_fallback_policy_outside_the_frozen_enum_is_rejected(bus):
    """N5. Invariants 6 and 7: ``explicit`` is not a frozen policy."""
    outcome = bus.submit(capability_request(constraints={"fallback_policy": "explicit"}))
    assert code_of(outcome) == "REQUEST_SCHEMA_VIOLATION"


def test_n6_a_lowercase_data_classification_is_rejected(bus):
    """N6."""
    outcome = bus.submit(
        capability_request(
            constraints={"fallback_policy": "none", "data_classification": "internal"}
        )
    )
    assert code_of(outcome) == "REQUEST_SCHEMA_VIOLATION"


def test_n7_a_bare_number_max_cost_is_rejected(bus):
    """N7. ``max_cost`` is ``{amount, currency}`` or null, never a scalar."""
    outcome = bus.submit(capability_request(constraints={"fallback_policy": "none", "max_cost": 5}))
    assert code_of(outcome) == "REQUEST_SCHEMA_VIOLATION"


def test_a_well_formed_max_cost_is_admitted(bus):
    outcome = bus.submit(
        capability_request(
            constraints={"fallback_policy": "none", "max_cost": {"amount": 5, "currency": "BRL"}}
        )
    )
    assert outcome.accepted


def test_n8_a_missing_idempotency_key_is_rejected(bus):
    """N8."""
    request = capability_request()
    del request["idempotency_key"]
    outcome = bus.submit(request)
    assert code_of(outcome) == "REQUEST_SCHEMA_VIOLATION"
    pointers = {v["pointer"] for v in outcome.error["details"]["violations"]}
    assert pointers == {""}


@pytest.mark.parametrize(
    "field",
    ["request_id", "correlation_id", "caller", "capability", "input", "constraints",
     "governance", "evidence"],
)
def test_every_required_field_is_required(bus, field):
    request = capability_request()
    del request[field]
    assert not bus.submit(request).accepted


def test_a_malformed_deadline_is_rejected(bus):
    """``format: date-time`` is asserted, not annotated. ``"tomorrow"`` is not a deadline."""
    outcome = bus.submit(
        capability_request(constraints={"fallback_policy": "none", "deadline": "tomorrow"})
    )
    assert code_of(outcome) == "REQUEST_SCHEMA_VIOLATION"


def test_a_valid_deadline_is_admitted(bus):
    outcome = bus.submit(
        capability_request(
            constraints={"fallback_policy": "none", "deadline": "2026-08-21T12:00:00Z"}
        )
    )
    assert outcome.accepted


# -- N13: the contract version ---------------------------------------------


def test_n13_an_unsupported_contract_version_is_rejected(bus):
    """N13, and note it is a *kernel* rule, not a schema rule.

    ``^v[0-9]+$`` admits ``v2`` perfectly well -- the frozen schema has no opinion
    about which versions exist. The kernel implements ``v1``, reads that from the
    schema's own ``x-ginkgo-contract-version``, and rejects anything else.
    """
    outcome = bus.submit(
        capability_request(capability={"name": "system.echo.execute", "contract_version": "v2"})
    )
    assert code_of(outcome) == "UNSUPPORTED_CONTRACT_VERSION"
    assert outcome.error["category"] == "VALIDATION"


def test_a_malformed_contract_version_is_caught_by_the_schema(bus):
    outcome = bus.submit(
        capability_request(capability={"name": "system.echo.execute", "contract_version": "1.0"})
    )
    assert code_of(outcome) == "REQUEST_SCHEMA_VIOLATION"


def test_the_supported_version_is_read_from_the_schema(contracts):
    assert contracts.supported_contract_version == "v1"


# -- P8: every caller class enters identically ------------------------------


@pytest.mark.parametrize("actor_type", ACTOR_TYPES)
def test_p8_every_actor_type_gets_equivalent_handling(bus, downstream, actor_type):
    """P8. Invariant 1: WhatsApp, n8n, Hermes, OpenClaw, Sylvia, API and MCP are equal.

    Equivalent means *identical but for the caller and the retry key*: same status,
    same routing, same output. ``actor_type`` is audit metadata and never privilege.
    """
    outcome = bus.submit(
        capability_request(
            caller={
                "actor_id": "actor-1",
                "actor_type": actor_type,
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
            },
            idempotency_key="idem-%s" % actor_type,
        )
    )
    assert outcome.accepted
    assert outcome.status == "SUCCESS"
    assert dict(outcome.result["routing"]) == {
        "adapter_id": None,
        "domain": "system",
        "fallback_used": False,
        "provider": None,
        "routing_reason": "scripted deterministic route",
    }


def test_p8_no_actor_type_is_privileged(bus):
    """The results differ only where the requests differed."""
    results = {}
    for actor_type in ACTOR_TYPES:
        outcome = bus.submit(
            capability_request(
                caller={
                    "actor_id": "actor-1",
                    "actor_type": actor_type,
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                },
                idempotency_key="idem-%s" % actor_type,
            )
        )
        body = dict(outcome.result)
        results[actor_type] = body
    baseline = results["API"]
    for actor_type, body in results.items():
        assert body == baseline, "%s was handled differently" % actor_type


def test_an_unknown_actor_type_is_rejected(bus):
    """The enum is frozen; an unlisted caller class is not a caller class."""
    outcome = bus.submit(
        capability_request(
            caller={
                "actor_id": "a",
                "actor_type": "ROOT",
                "tenant_id": "t",
                "workspace_id": "w",
            }
        )
    )
    assert code_of(outcome) == "REQUEST_SCHEMA_VIOLATION"


# -- boundary robustness (adversarial-review regressions) -------------------


def test_a_deeply_nested_request_is_rejected_not_crashed(bus):
    """A caller must not be able to take the boundary down with a legal document.

    ``input`` is ``additionalProperties: true`` and the frozen schema sets no
    depth bound, so a few thousand levels of nesting is a *valid* request. The
    forbidden-field scan and the idempotency fingerprint both recurse, and before
    this limit existed the pair raised ``RecursionError`` out of ``submit()``.
    """
    deep = {}
    cursor = deep
    for _ in range(3000):
        cursor["n"] = {}
        cursor = cursor["n"]

    outcome = bus.submit(capability_request(input=deep))
    assert not outcome.accepted
    assert code_of(outcome) == "REQUEST_TOO_DEEPLY_NESTED"


def test_the_depth_limit_does_not_reject_ordinary_nesting(bus):
    payload = {"order": {"lines": [{"item": {"attributes": {"colour": "red"}}}]}}
    assert bus.submit(capability_request(input=payload)).accepted


def test_depth_is_measured_without_recursing():
    """Measuring the depth of an over-deep document must not itself overflow."""
    from ginkgo_runtime.bus.admission import MAX_DOCUMENT_DEPTH, document_depth

    deep = {}
    cursor = deep
    for _ in range(50_000):
        cursor["n"] = {}
        cursor = cursor["n"]
    assert document_depth(deep) > MAX_DOCUMENT_DEPTH


def test_a_deeply_nested_request_is_rejected_before_it_is_fingerprinted(bus, downstream):
    deep = {}
    cursor = deep
    for _ in range(3000):
        cursor["n"] = {}
        cursor = cursor["n"]

    bus.submit(capability_request(input=deep))
    assert downstream.call_count == 0
