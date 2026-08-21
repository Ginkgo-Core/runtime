"""The return leg -- status mapping, the validation veto, and N15/N16.

RK-01 absorbs RK-05 so that the code building the envelope and the code
projecting it for the caller are the same code. These tests cover both halves,
plus the two invariant-17 negatives from ``reference-slice.md`` section 5.
"""

from __future__ import annotations

import pytest

from ginkgo_runtime.bus import WITHHELD_FROM_CALLER, DeterministicDownstream, caller_projection
from ginkgo_runtime.bus.bus import CapabilityBus
from ginkgo_runtime.bus.normalization import INDEPENDENT_VALIDATION_ERROR_CODE

from .conftest import capability_request, execution_result, routed_outcome


def bus_returning(contracts, outcome):
    downstream = DeterministicDownstream(by_capability={"system.echo.execute": outcome})
    return CapabilityBus(downstream=downstream, contracts=contracts), downstream


# -- the declared status mapping -------------------------------------------


def test_the_mapping_is_read_from_the_frozen_schema(contracts):
    """Not a table in this package.

    ``runtime-execution-result.schema.json``: the mapping *"is declared in this
    schema under x-ginkgo-status-mapping and is applied by the Capability Bus
    (RK-01), not by the executor."*
    """
    mapping = contracts.status_mapping
    assert mapping.status_for("succeeded", partial=False) == "SUCCESS"
    assert mapping.status_for("succeeded", partial=True) == "PARTIAL"
    assert mapping.status_for("failed", partial=False) == "ERROR"
    assert mapping.status_for("timed_out", partial=False) == "ERROR"
    assert mapping.status_for("cancelled", partial=False) == "BLOCKED"


def test_an_undeclared_executor_state_is_not_invented(contracts):
    from ginkgo_runtime.contracts import ContractsUnavailableError

    with pytest.raises(ContractsUnavailableError):
        contracts.status_mapping.status_for("exploded", partial=False)


@pytest.mark.parametrize(
    ("state", "status", "extra"),
    [
        ("succeeded", "SUCCESS", {}),
        ("succeeded", "PARTIAL", {"partial": True}),
        (
            "failed",
            "ERROR",
            {"error": {"code": "X", "message": "m", "category": "PROVIDER", "retryable": False}},
        ),
        (
            "timed_out",
            "ERROR",
            {"error": {"code": "T", "message": "m", "category": "TIMEOUT", "retryable": True}},
        ),
        ("cancelled", "BLOCKED", {}),
    ],
)
def test_every_terminal_state_maps_as_declared(contracts, state, status, extra):
    execution = execution_result(state=state, **extra)
    bus, _ = bus_returning(contracts, routed_outcome(execution=execution))
    outcome = bus.submit(capability_request())
    assert outcome.accepted
    assert outcome.status == status


@pytest.mark.parametrize("state", ["queued", "running"])
def test_a_non_terminal_state_produces_no_result(contracts, state):
    """``queued`` and ``running`` are not results, and the Bus refuses to invent one."""
    execution = execution_result(state=state, terminal=False)
    del execution["telemetry"]
    bus, _ = bus_returning(contracts, routed_outcome(execution=execution))
    outcome = bus.submit(capability_request())

    assert not outcome.accepted
    assert outcome.error["code"] == "EXECUTION_NOT_TERMINAL"


def test_the_executor_never_performs_the_mapping(contracts):
    """A ``status`` field on the execution result is not a thing, and is not read.

    ``RuntimeExecutionResult`` is ``additionalProperties: false``, so an executor
    trying to hand RK-01 a pre-mapped status is rejected outright rather than
    quietly obeyed.
    """
    execution = execution_result(status="SUCCESS")
    bus, _ = bus_returning(contracts, routed_outcome(execution=execution))
    outcome = bus.submit(capability_request())
    assert outcome.error["code"] == "DOWNSTREAM_CONTRACT_VIOLATION"


# -- independent_validation -------------------------------------------------


@pytest.mark.parametrize("satisfied", [False, None])
def test_unsatisfied_independent_validation_never_yields_success(contracts, satisfied):
    """*"An executor's self-reported succeeded is not by itself sufficient evidence."*"""
    execution = execution_result(
        independent_validation={"required": True, "satisfied": satisfied}
    )
    bus, _ = bus_returning(contracts, routed_outcome(execution=execution))
    outcome = bus.submit(capability_request())

    assert outcome.accepted
    assert outcome.status != "SUCCESS"
    assert outcome.status == "ERROR"
    assert outcome.result["error"]["code"] == INDEPENDENT_VALIDATION_ERROR_CODE


def test_unsatisfied_independent_validation_also_vetoes_partial(contracts):
    """PARTIAL asserts partial success on the same unverified evidence."""
    execution = execution_result(
        partial=True, independent_validation={"required": True, "satisfied": False}
    )
    bus, _ = bus_returning(contracts, routed_outcome(execution=execution))
    assert bus.submit(capability_request()).status == "ERROR"


def test_satisfied_independent_validation_permits_success(contracts):
    execution = execution_result(
        independent_validation={"required": True, "satisfied": True, "validator_ref": "v-1"}
    )
    bus, _ = bus_returning(contracts, routed_outcome(execution=execution))
    assert bus.submit(capability_request()).status == "SUCCESS"


def test_validation_that_is_not_required_does_not_veto(contracts):
    execution = execution_result(independent_validation={"required": False, "satisfied": None})
    bus, _ = bus_returning(contracts, routed_outcome(execution=execution))
    assert bus.submit(capability_request()).status == "SUCCESS"


def test_the_veto_does_not_rewrite_a_non_success_state(contracts):
    """A ``cancelled`` run stays ``BLOCKED``.

    The veto withholds an unproven claim; it does not discard true information.
    Turning this into ERROR would report an executor failure that did not happen.
    """
    execution = execution_result(
        state="cancelled", independent_validation={"required": True, "satisfied": False}
    )
    bus, _ = bus_returning(contracts, routed_outcome(execution=execution))
    assert bus.submit(capability_request()).status == "BLOCKED"


# -- N16: the caller projection ---------------------------------------------


def test_n16_the_caller_projection_withholds_routing_provenance(contracts):
    """N16, an invariant-17 test.

    ``capability-result.schema.json`` on its own routing block: *"Caller-facing
    projections MUST NOT expose adapter_id/provider."*
    """
    outcome_ = routed_outcome(adapter_id="shopee-open-api", provider="shopee-open-platform")
    bus, _ = bus_returning(contracts, outcome_)
    outcome = bus.submit(capability_request())

    internal = outcome.result["routing"]
    assert internal["adapter_id"] == "shopee-open-api"
    assert internal["provider"] == "shopee-open-platform"

    projected = outcome.for_caller()["routing"]
    assert "adapter_id" not in projected
    assert "provider" not in projected


def test_n16_absent_rather_than_null(contracts):
    """The fields are *removed*, and null would be worse than absent.

    A deterministic local skill has genuinely ``null`` adapter and provider. If
    withholding also rendered as null, a caller could no longer tell "no adapter
    exists" from "you are not allowed to know" -- and the projection would look
    conformant while destroying that distinction.
    """
    outcome_ = routed_outcome(adapter_id="an-adapter", provider="a-provider")
    bus, _ = bus_returning(contracts, outcome_)
    projected = bus.submit(capability_request()).for_caller()

    assert set(projected["routing"]) == {"domain", "fallback_used", "routing_reason"}


def test_the_projection_is_not_a_capability_result_and_that_is_the_contract(contracts):
    """Stated so nobody later "fixes" it by putting the fields back.

    ``routing.required`` lists ``adapter_id`` and ``provider``, so a projection
    that withholds them cannot validate as ``CapabilityResult v1``. That is the
    contract instructing its own projection.
    """
    outcome_ = routed_outcome(adapter_id="a", provider="p")
    bus, _ = bus_returning(contracts, outcome_)
    outcome = bus.submit(capability_request())

    assert contracts.capability_result.is_valid(outcome.result)
    assert not contracts.capability_result.is_valid(outcome.for_caller())


def test_the_projection_does_not_mutate_the_internal_result(contracts):
    bus, _ = bus_returning(contracts, routed_outcome(adapter_id="a", provider="p"))
    outcome = bus.submit(capability_request())

    outcome.for_caller()
    assert outcome.result["routing"]["adapter_id"] == "a"
    assert outcome.result["routing"]["provider"] == "p"


def test_the_withheld_set_is_exactly_the_two_the_contract_names():
    assert WITHHELD_FROM_CALLER == ("adapter_id", "provider")


def test_a_projection_of_a_result_without_routing_is_harmless():
    assert caller_projection({"status": "ERROR"})["routing"] == {}


# -- N15: nothing leaks into an error --------------------------------------

SECRET = "AKIAIOSFODNN7EXAMPLE-super-secret-value"


def test_n15_a_rejected_value_is_never_echoed_into_the_error(bus):
    """N15, an invariant-17 test.

    ``error.schema.json``: *"Secrets, credentials, raw endpoints and provider
    payloads MUST NOT appear in message or details."* A validator that helpfully
    quotes the offending value is the obvious way to violate that, because the
    offending value is precisely the thing that might be a credential.
    """
    outcome = bus.submit(capability_request(input={"api_key": SECRET}))
    assert not outcome.accepted

    body = repr(outcome.error)
    assert SECRET not in body
    assert "AKIA" not in body


def test_n15_a_schema_violation_reports_pointers_and_not_values(bus):
    outcome = bus.submit(
        capability_request(constraints={"fallback_policy": SECRET})
    )
    assert not outcome.accepted
    assert SECRET not in repr(outcome.error)
    violations = outcome.error["details"]["violations"]
    assert all(set(v) == {"keyword", "pointer"} for v in violations)


def test_n15_every_error_the_bus_emits_validates_against_the_frozen_error(bus, contracts):
    """Whatever the Bus rejects with, it is ``Error v1`` -- checked, not assumed."""
    probes = [
        capability_request(capability={"name": "system.echo", "contract_version": "v1"}),
        capability_request(input={"provider_hint": "x"}),
        capability_request(capability={"name": "system.echo.execute", "contract_version": "v9"}),
    ]
    for probe in probes:
        outcome = bus.submit(probe)
        assert not outcome.accepted
        assert contracts.error.is_valid(outcome.error), outcome.error


def test_a_conflict_error_carries_no_payload(bus):
    """The conflicting payload is what differed; it is not what gets reported."""
    bus.submit(capability_request())
    outcome = bus.submit(capability_request(input={"api_key": "not-scanned-anyway"}))
    # The forbidden-field gate fires first here, which is itself the point: a
    # conflicting payload never reaches the ledger without being admissible.
    assert outcome.error["code"] == "CALLER_SELECTED_IMPLEMENTATION"

    outcome = bus.submit(capability_request(input={"message": SECRET}))
    assert outcome.error["code"] == "IDEMPOTENCY_CONFLICT"
    assert SECRET not in repr(outcome.error)


# -- the downstream seam ----------------------------------------------------


def test_a_blocked_decision_does_not_become_a_fabricated_result(contracts):
    """A real finding, surfaced rather than papered over.

    ``CapabilityResult v1`` requires ``skill_id``, ``skill_version`` and
    ``execution_id`` as non-empty strings; a decision that resolved to nothing has
    none of them. Reference slice N12 nonetheless expects an unknown capability to
    yield a ``BLOCKED`` result. The two cannot both hold.

    RK-01 does not settle that here -- N9-N12 belong to ``#40`` and ``#41``. It
    fails closed instead: no invented identity, and the tension is reported.
    """
    from ginkgo_runtime.bus import DownstreamOutcome, RouteView

    blocked = DownstreamOutcome(
        decision=RouteView(outcome="UNKNOWN_CAPABILITY", routing_reason="not in catalog"),
        execution=None,
    )
    bus, _ = bus_returning(contracts, blocked)
    outcome = bus.submit(capability_request())

    assert not outcome.accepted
    assert outcome.error["code"] == "DOWNSTREAM_CONTRACT_VIOLATION"
    assert outcome.result is None


def test_a_routed_decision_without_a_skill_is_refused(contracts):
    execution = execution_result()
    outcome_ = routed_outcome(execution=execution, skill_id=None)
    bus, _ = bus_returning(contracts, outcome_)
    outcome = bus.submit(capability_request())

    assert outcome.error["code"] == "DOWNSTREAM_CONTRACT_VIOLATION"


def test_a_malformed_execution_result_is_refused(contracts):
    """The Bus does not trust the seam any more than it trusts the caller."""
    outcome_ = routed_outcome(execution={"state": "succeeded"})
    bus, _ = bus_returning(contracts, outcome_)
    outcome = bus.submit(capability_request())

    assert outcome.error["code"] == "DOWNSTREAM_CONTRACT_VIOLATION"
    assert outcome.error["details"]["schema_ref"].endswith("runtime-execution-result.schema.json")


def test_the_executor_reference_never_reaches_the_result(contracts):
    """``executor_ref`` is *"recorded for audit ONLY ... never caller-visible"*.

    ``CapabilityResult v1`` has no field for it, so the guarantee is structural.
    Asserted anyway, because "there is nowhere to put it" is only true until
    somebody adds somewhere to put it.
    """
    execution = execution_result(executor_ref="n8n-worker-7")
    bus, _ = bus_returning(contracts, routed_outcome(execution=execution))
    outcome = bus.submit(capability_request())

    assert "n8n-worker-7" not in repr(outcome.result)
    assert "n8n-worker-7" not in repr(outcome.for_caller())


def test_every_emitted_result_validates_against_the_frozen_contract(contracts):
    """The Bus checks what it built. A boundary that emits an unchecked envelope
    is asking the caller to discover the defect."""
    for state, extra in (
        ("succeeded", {}),
        ("succeeded", {"partial": True}),
        ("cancelled", {}),
    ):
        execution = execution_result(state=state, **extra)
        bus, _ = bus_returning(contracts, routed_outcome(execution=execution))
        outcome = bus.submit(capability_request())
        assert contracts.capability_result.is_valid(outcome.result)


def test_the_result_carries_the_request_identity(contracts):
    bus, _ = bus_returning(contracts, routed_outcome())
    outcome = bus.submit(capability_request())

    assert outcome.result["request_id"] == "req-0001"
    assert outcome.result["correlation_id"] == "corr-0001"
    assert outcome.result["execution_id"] == "exec-0001"
    assert dict(outcome.result["capability"]) == {
        "name": "system.echo.execute",
        "contract_version": "v1",
    }


def test_a_caller_cannot_crash_the_boundary_with_an_oversized_error(bus, contracts):
    """Adversarial-review regression, and a denial of service if left unfixed.

    ``error.schema.json`` caps ``message`` at 2048 characters. A request naming
    four hundred forbidden fields produced a longer one, so the Bus failed its own
    error envelope and raised -- meaning any caller could take the boundary down
    with a perfectly legal request. The limit is read from the frozen contract.
    """
    outcome = bus.submit(
        capability_request(input={"provider_%03d" % i: "x" for i in range(400)})
    )

    assert outcome.error["code"] == "CALLER_SELECTED_IMPLEMENTATION"
    assert len(outcome.error["message"]) <= contracts.error_message_max_length
    assert outcome.error["message"].endswith("... (truncated)")
    assert contracts.error.is_valid(outcome.error)


def test_the_message_limit_is_read_from_the_frozen_contract(contracts):
    assert contracts.error_message_max_length == 2048


def test_a_short_message_is_not_truncated(bus):
    outcome = bus.submit(capability_request(input={"provider_hint": "x"}))
    assert "truncated" not in outcome.error["message"]


# -- the seam is not trusted (adversarial-review regressions) ----------------


def test_a_downstream_that_raises_is_normalized_not_propagated(contracts):
    """A stack trace is not a ``CapabilityResult``, and the seam is foreign code."""

    class Exploding:
        def dispatch(self, request):
            raise RuntimeError("boom")

    bus = CapabilityBus(downstream=Exploding(), contracts=contracts)
    outcome = bus.submit(capability_request())

    assert not outcome.accepted
    assert outcome.error["code"] == "DOWNSTREAM_CONTRACT_VIOLATION"
    assert outcome.error["details"]["exception_type"] == "RuntimeError"
    assert contracts.error.is_valid(outcome.error)


def test_a_downstream_exception_message_never_reaches_the_caller(contracts):
    """N15 applies to downstream text too, and that is where credentials live.

    An exception raised deep in an adapter routinely quotes a connection string.
    Only the exception's *type* crosses the boundary.
    """

    class Leaking:
        def dispatch(self, request):
            raise RuntimeError("postgres://user:hunter2@db.internal:5432/prod refused")

    bus = CapabilityBus(downstream=Leaking(), contracts=contracts)
    body = repr(bus.submit(capability_request()).error)

    assert "hunter2" not in body
    assert "postgres" not in body
    assert "db.internal" not in body


def test_a_downstream_returning_the_wrong_type_is_refused(contracts):
    class Garbage:
        def dispatch(self, request):
            return "not an outcome"

    bus = CapabilityBus(downstream=Garbage(), contracts=contracts)
    outcome = bus.submit(capability_request())

    assert outcome.error["code"] == "DOWNSTREAM_CONTRACT_VIOLATION"
    assert outcome.error["details"]["returned_type"] == "str"


def test_a_failed_dispatch_claims_no_idempotency_key(contracts):
    """A downstream failure must not burn the caller's retry key."""

    class Exploding:
        def dispatch(self, request):
            raise RuntimeError("boom")

    bus = CapabilityBus(downstream=Exploding(), contracts=contracts)
    assert not bus.submit(capability_request()).accepted

    bus.downstream = DeterministicDownstream(
        by_capability={"system.echo.execute": routed_outcome()}
    )
    retry = bus.submit(capability_request())
    assert retry.accepted
    assert retry.replayed is False
