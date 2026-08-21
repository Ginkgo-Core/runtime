"""The reference slice, end to end — the positives and the negatives.

``reference-slice.md`` section 5: *"The slice is not done when the happy path
passes. It is done when the negatives pass."*

Everything here runs the **whole chain**: RK-01 → RK-02 → RK-04 → RK-08A →
`rk-reference-echo` / `rk-echo-transform` → RK-06. Nothing is stubbed except the
supplied business-policy verdict, which RK-04 records and never derives.

Zero network, zero credentials, zero writes, zero containers, zero providers —
and those four are asserted by *execution*, not by reading the descriptor back.
"""

from __future__ import annotations

import builtins
import socket

import pytest

from ginkgo_runtime.evidence import NEGATIVE_PROOFS, EvidenceWriteError
from ginkgo_runtime.slice import build_reference_kernel

from .conftest import slice_request

ACTOR_TYPES = ("HUMAN", "WHATSAPP", "N8N", "HERMES", "OPENCLAW", "SYLVIA", "API", "MCP")

SECRET = "AKIAIOSFODNN7EXAMPLE-super-secret-value"


# ===========================================================================
# The happy path
# ===========================================================================


def test_the_whole_chain_runs(slice_run):
    """One request, one capability, one skill, one execution, one result."""
    assert slice_run.status == "SUCCESS"
    assert slice_run.outcome.error is None
    assert slice_run.outcome.result["output"]["echoed"] == "hello from the reference slice"


def test_p1_the_request_validates_against_the_frozen_contract(reference_kernel):
    """P1."""
    assert reference_kernel.contracts.capability_request.is_valid(slice_request())


def test_p2_the_result_validates_against_the_frozen_contract(reference_kernel, slice_run):
    """P2."""
    assert reference_kernel.contracts.capability_result.is_valid(slice_run.outcome.result)


def test_p3_success_with_no_fallback_and_a_recorded_reason(slice_run):
    """P3."""
    routing = slice_run.outcome.result["routing"]
    assert slice_run.status == "SUCCESS"
    assert routing["fallback_used"] is False
    assert routing["routing_reason"]
    assert slice_run.decision["fallback"] == {"policy": "none", "used": False}


def test_p4_adapter_and_provider_are_null_honestly(slice_run):
    """P4. Null *with a reason* — the whole reason the descriptor supports it."""
    resolved = slice_run.decision["resolved"]
    assert resolved["adapter_id"] is None
    assert resolved["provider"] is None
    assert len(resolved["adapter_not_applicable_reason"]) >= 8
    assert len(resolved["provider_not_applicable_reason"]) >= 8
    assert resolved["tool_id"] == "rk-echo-transform"
    assert resolved["skill_id"] == "rk-reference-echo"


def test_the_three_entities_that_exist_are_distinct(slice_run):
    """CAPABILITY != SKILL != TOOL, and adapter and provider genuinely absent."""
    resolved = slice_run.decision["resolved"]
    assert slice_run.request["capability"]["name"] == "system.echo.execute"
    assert resolved["skill_id"] == "rk-reference-echo"
    assert resolved["tool_id"] == "rk-echo-transform"
    assert len({"system.echo.execute", resolved["skill_id"], resolved["tool_id"]}) == 3


# ===========================================================================
# Exactly one Evidence record
# ===========================================================================


def test_p5_exactly_one_evidence_record_and_the_result_points_at_it(
    reference_kernel, slice_run
):
    """P5."""
    assert reference_kernel.evidence.record_count == 1
    assert slice_run.packet is not None
    assert slice_run.outcome.result["evidence_refs"] == [slice_run.packet.evidence_id]


def test_the_evidence_is_a_frozen_evidence_v1(reference_kernel, slice_run):
    """It writes ``Evidence v1``. It does not define a record of its own."""
    assert reference_kernel.contracts.evidence.is_valid(slice_run.packet.evidence)
    assert slice_run.packet.evidence["record_type"] == "evidence"


def test_the_evidence_kind_is_test_and_never_an_api_observation(slice_run):
    """*"A green result is TEST / EVAL evidence, never API_OBSERVATION."*"""
    assert slice_run.packet.evidence["kind"] == "TEST"


def test_the_evidence_carries_the_negative_proofs(slice_run):
    """An evidence record that only carries what succeeded invites an overclaim."""
    provenance = slice_run.packet.evidence["provenance"]
    for key, value in NEGATIVE_PROOFS.items():
        assert provenance[key] == value
    assert provenance["network"] == "OFF"
    assert provenance["live_provider_validation_proven"] is False


def test_the_evidence_carries_the_minimum_recorded_set(slice_run):
    """`ai-stack#43`'s list, field by field."""
    provenance = slice_run.packet.evidence["provenance"]
    for field in (
        "correlation_id",
        "capability",
        "skill_id",
        "skill_version",
        "binding_ref",
        "adapter_ref",
        "provider_binding_ref",
        "executor_ref",
        "policy_decision_ref",
        "input_fingerprint",
        "started_at",
        "ended_at",
        "result_status",
        "error_class",
        "cost_events",
        "artifact_hashes",
    ):
        assert field in provenance, field


def test_the_executor_ref_is_recorded_separately_from_the_provider_ref(slice_run):
    """`#43` asks for exactly this, and here the provider is genuinely absent."""
    provenance = slice_run.packet.evidence["provenance"]
    assert provenance["executor_ref"] == "rk-in-process-executor"
    assert provenance["provider_binding_ref"] is None
    assert provenance["adapter_ref"] is None


def test_the_evidence_never_promotes_to_knowledge(slice_run):
    """Evidence != Knowledge (invariant 12). No code path sets anything else."""
    assert slice_run.packet.evidence["knowledge_promotion"] == {
        "status": "NOT_REQUESTED",
        "knowledge_ref": None,
    }


def test_the_evidence_carries_a_fingerprint_and_never_the_input(slice_run):
    """*"Never the input itself."*"""
    message = slice_run.request["input"]["message"]
    provenance = slice_run.packet.evidence["provenance"]
    assert len(provenance["input_fingerprint"]) == 64
    assert message not in repr(slice_run.packet.evidence)


def test_the_evidence_carries_no_policy_snapshot(slice_run):
    """A decision ref, never the policy's contents."""
    body = repr(slice_run.packet.evidence)
    assert "rk-reference-slice-policy@1#" in slice_run.packet.evidence["provenance"][
        "policy_decision_ref"
    ]
    assert "grants no business authority" not in body


# ===========================================================================
# Lifecycle events
# ===========================================================================


def test_p6_every_transition_emitted_one_event(reference_kernel, slice_run):
    """P6. ``event.schema.json`` is the seventh frozen core schema; this is its
    first consumer anywhere in the ecosystem (BLK-09)."""
    events = slice_run.packet.events
    assert [e["event_type"] for e in events] == [
        "runtime.execution.queued",
        "runtime.execution.running",
        "runtime.execution.succeeded",
    ]
    for event in events:
        assert reference_kernel.contracts.event.is_valid(event)


def test_every_event_carries_timestamp_actor_and_reason(slice_run):
    """Verbatim from `#7`: *"Toda transição precisa ser persistida com timestamp,
    ator e motivo."*"""
    for event in slice_run.packet.events:
        assert event["occurred_at"]
        assert event["actor"]["actor_id"]
        assert event["actor"]["actor_type"]
        assert event["payload"]["reason"]
        assert event["evidence_refs"] == [slice_run.packet.evidence_id]


def test_no_status_exists_only_inside_an_execution(slice_run):
    """*"Nenhum status deve existir apenas dentro de uma execução n8n."*

    Every state the executor passed through is persisted as an event, so the
    execution result is not the only place any of them exists.
    """
    executed = {t.split(".")[-1] for t in [e["event_type"] for e in slice_run.packet.events]}
    assert slice_run.execution["state"] in executed


def test_the_events_are_ordered_in_time(slice_run):
    stamps = [e["occurred_at"] for e in slice_run.packet.events]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)


# ===========================================================================
# P7 — idempotent replay writes no second evidence
# ===========================================================================


def test_p7_a_replay_returns_the_same_result_and_writes_no_second_record(reference_kernel):
    """P7, both halves."""
    first = reference_kernel.submit(slice_request())
    assert reference_kernel.evidence.record_count == 1

    second = reference_kernel.submit(slice_request(request_id="req-slice-0002"))

    assert second.outcome.replayed is True
    assert dict(second.outcome.result) == dict(first.outcome.result)
    assert reference_kernel.evidence.record_count == 1
    assert len(reference_kernel.downstream.executions) == 1


def test_a_replay_never_reaches_the_executor(reference_kernel):
    """Exactly-once is a consequence of where the seam is, not of a later check.

    RK-01's ledger short-circuits before dispatch, so the executor is never
    invoked and RK-06 is never reached.
    """
    reference_kernel.submit(slice_request())
    for index in range(5):
        reference_kernel.submit(slice_request(request_id="req-replay-%d" % index))

    assert len(reference_kernel.downstream.decisions) == 1
    assert len(reference_kernel.downstream.executions) == 1
    assert reference_kernel.evidence.record_count == 1


def test_the_writer_also_refuses_a_second_record_on_its_own(reference_kernel, slice_run):
    """A guarantee resting on one mechanism is one nobody can falsify."""
    with pytest.raises(EvidenceWriteError) as excinfo:
        reference_kernel.evidence.record(
            request=slice_run.request,
            decision=slice_run.decision,
            execution=slice_run.execution,
            result=slice_run.outcome.result,
            transitions=(),
        )
    assert "no second record" in str(excinfo.value)


def test_a_conflicting_payload_under_the_same_key_writes_nothing(reference_kernel):
    """N14, end to end."""
    reference_kernel.submit(slice_request())
    conflict = reference_kernel.submit(
        slice_request(request_id="req-slice-0003", input={"message": "different"})
    )

    assert conflict.outcome.error["code"] == "IDEMPOTENCY_CONFLICT"
    assert reference_kernel.evidence.record_count == 1


# ===========================================================================
# P8 — caller equivalence
# ===========================================================================


@pytest.mark.parametrize("actor_type", ACTOR_TYPES)
def test_p8_every_caller_class_gets_equivalent_handling(actor_type):
    """P8. Invariant 1: WhatsApp, n8n, Hermes, OpenClaw, Sylvia, API and MCP are equal.

    Each runs in its own kernel so the idempotency ledger cannot make the second
    caller look different by replaying the first.
    """
    kernel = build_reference_kernel()
    run = kernel.submit(
        slice_request(
            caller={
                "actor_id": "actor-1",
                "actor_type": actor_type,
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
            }
        )
    )
    assert run.status == "SUCCESS"
    assert kernel.evidence.record_count == 1
    assert run.outcome.result["output"]["echoed"] == "hello from the reference slice"


def test_p8_the_results_differ_only_where_the_requests_differed():
    """`actor_type` is audit metadata and never privilege."""
    results = {}
    for actor_type in ACTOR_TYPES:
        kernel = build_reference_kernel()
        run = kernel.submit(
            slice_request(
                caller={
                    "actor_id": "actor-1",
                    "actor_type": actor_type,
                    "tenant_id": "tenant-1",
                    "workspace_id": "workspace-1",
                }
            )
        )
        results[actor_type] = dict(run.outcome.result)

    baseline = results["API"]
    for actor_type, body in results.items():
        assert body == baseline, actor_type


@pytest.mark.parametrize("actor_type", ACTOR_TYPES)
def test_p8_the_evidence_records_the_caller_without_privileging_it(actor_type):
    kernel = build_reference_kernel()
    run = kernel.submit(
        slice_request(
            caller={
                "actor_id": "actor-1",
                "actor_type": actor_type,
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-1",
            }
        )
    )
    assert all(e["actor"]["actor_type"] == actor_type for e in run.packet.events)


# ===========================================================================
# Lifecycle: FEATURE in certification, FEATURE barred in production
# ===========================================================================


def test_feature_is_permitted_in_certification(slice_run):
    """The escape from the circularity, exercised for real.

    ``rk-reference-echo`` is ``FEATURE`` with ``routing_eligible: false`` and it
    routes — because ``certification`` consults neither flag and carries a
    recorded authorization instead.
    """
    assert slice_run.decision["lifecycle"]["status"] == "FEATURE"
    assert slice_run.decision["lifecycle"]["routing_eligible"] is False
    assert slice_run.decision["lifecycle"]["production_eligible"] is False
    assert slice_run.decision["execution_mode"] == "certification"
    assert slice_run.decision["resolved"]["outcome"] == "ROUTED"
    assert slice_run.decision["certification_authorization_ref"]


def test_n9_feature_is_barred_in_production():
    """N9. Invariant 16, the one lifecycle rule frozen in ``master``."""
    kernel = build_reference_kernel(
        execution_mode="production", certification_authorization_ref=None
    )
    run = kernel.submit(slice_request())

    assert run.decision["resolved"]["outcome"] == "BLOCKED_LIFECYCLE"
    assert run.outcome.accepted is False
    assert kernel.evidence.record_count == 0
    assert kernel.downstream.executions == []


def test_feature_is_barred_in_non_production():
    """Section 3 marks FEATURE "no" for caller traffic in both modes."""
    kernel = build_reference_kernel(
        execution_mode="non_production", certification_authorization_ref=None
    )
    run = kernel.submit(slice_request())

    assert run.decision["resolved"]["outcome"] == "BLOCKED_LIFECYCLE"
    assert kernel.evidence.record_count == 0


def test_certification_without_the_recorded_authorization_is_barred():
    """Otherwise it would be an unaudited bypass of the ladder."""
    kernel = build_reference_kernel(certification_authorization_ref=None)
    run = kernel.submit(slice_request())

    assert run.decision["resolved"]["outcome"] == "BLOCKED_LIFECYCLE"
    assert kernel.evidence.record_count == 0


def test_a_blocked_run_writes_no_evidence_and_runs_nothing():
    """Nothing executed, so there is nothing to write evidence about."""
    kernel = build_reference_kernel(policy_decision="DENY")
    run = kernel.submit(slice_request())

    assert run.decision["resolved"]["outcome"] == "BLOCKED_POLICY"
    assert kernel.evidence.record_count == 0
    assert kernel.downstream.executions == []


# ===========================================================================
# N10 / N11 — approval never clears a gate
# ===========================================================================


@pytest.mark.parametrize("gate_state", ["BLOCKED", "UNKNOWN"])
def test_n10_n11_an_approved_approval_never_clears_a_gate(gate_state):
    """N10 and N11, end to end. Invariant 5."""
    kernel = build_reference_kernel(
        approval_required=True,
        approval_state="APPROVED",
        external_gates=({"gate_id": "shopee_business_approval", "state": gate_state},),
    )
    run = kernel.submit(slice_request())

    assert run.decision["resolved"]["outcome"] == "BLOCKED_EXTERNAL_GATE"
    assert run.outcome.accepted is False
    assert kernel.evidence.record_count == 0


def test_a_clear_gate_and_an_approval_let_the_run_proceed():
    kernel = build_reference_kernel(
        approval_required=True,
        approval_state="APPROVED",
        external_gates=({"gate_id": "g", "state": "CLEAR"},),
    )
    run = kernel.submit(slice_request())

    assert run.status == "SUCCESS"
    assert kernel.evidence.record_count == 1


def test_a_pending_approval_blocks_and_names_the_approval():
    kernel = build_reference_kernel(approval_required=True, approval_state="PENDING")
    run = kernel.submit(slice_request())

    assert run.decision["resolved"]["outcome"] == "BLOCKED_APPROVAL"
    assert kernel.evidence.record_count == 0


# ===========================================================================
# N2 / N3 / N4 — a caller never selects an implementation
# ===========================================================================


@pytest.mark.parametrize(
    "field",
    ["provider", "adapter_id", "skill_id", "tool_id", "executor_id", "workflow_id", "endpoint"],
)
def test_n2_a_caller_cannot_name_an_implementation(reference_kernel, field):
    """N2, end to end. Invariants 17 and 18."""
    run = reference_kernel.submit(slice_request(**{field: "amazon"}))

    assert run.outcome.accepted is False
    assert run.outcome.error["category"] == "VALIDATION"
    assert reference_kernel.evidence.record_count == 0
    assert reference_kernel.downstream.decisions == []


@pytest.mark.parametrize("field", ["provider_hint", "endpoint_url", "commands"])
def test_n3_prefix_matching_catches_the_near_misses(reference_kernel, field):
    """N3. An exact-name check passes all three."""
    run = reference_kernel.submit(slice_request(input={"message": "hi", field: "amazon"}))

    assert run.outcome.error["code"] == "CALLER_SELECTED_IMPLEMENTATION"
    assert reference_kernel.evidence.record_count == 0


@pytest.mark.parametrize("field", ["credential", "api_key", "access_token", "shell_command"])
def test_n4_a_caller_cannot_supply_a_credential_surface(reference_kernel, field):
    """N4."""
    run = reference_kernel.submit(slice_request(input={"message": "hi", field: SECRET}))

    assert run.outcome.error["code"] == "CALLER_SELECTED_IMPLEMENTATION"
    assert SECRET not in repr(run.outcome.error)


def test_the_caller_never_learns_which_skill_ran_from_the_projection(slice_run):
    """The caller named a capability and gets a result. It selected nothing."""
    caller_view = slice_run.caller_view
    assert caller_view["capability"]["name"] == "system.echo.execute"
    assert "adapter_id" not in caller_view["routing"]
    assert "provider" not in caller_view["routing"]


# ===========================================================================
# N15 / N16 — nothing leaks
# ===========================================================================


def test_n15_no_error_body_carries_secret_material(reference_kernel):
    """N15."""
    probes = [
        slice_request(input={"message": "hi", "api_key": SECRET}),
        slice_request(capability={"name": "system.echo", "contract_version": "v1"}),
        slice_request(constraints={"fallback_policy": SECRET}),
    ]
    for probe in probes:
        run = reference_kernel.submit(probe)
        assert run.outcome.accepted is False
        body = repr(run.outcome.error)
        assert SECRET not in body
        assert "AKIA" not in body
        assert reference_kernel.contracts.error.is_valid(run.outcome.error)


def test_n16_the_caller_projection_withholds_routing_provenance(slice_run):
    """N16. And here both are honestly null, which is the harder case.

    Withholding has to be distinguishable from *not applicable*: the internal
    result says ``null``, and the projection says nothing at all.
    """
    internal = slice_run.outcome.result["routing"]
    projected = slice_run.caller_view["routing"]

    assert "adapter_id" in internal and internal["adapter_id"] is None
    assert "provider" in internal and internal["provider"] is None
    assert "adapter_id" not in projected
    assert "provider" not in projected
    assert set(projected) == {"domain", "fallback_used", "routing_reason"}


def test_the_executor_reference_never_reaches_the_caller(slice_run):
    """*"Recorded for audit ONLY ... never caller-visible."*"""
    assert slice_run.execution["executor_ref"] == "rk-in-process-executor"
    assert "rk-in-process-executor" not in repr(slice_run.outcome.result)
    assert "rk-in-process-executor" not in repr(slice_run.caller_view)


# ===========================================================================
# The normalized result
# ===========================================================================


def test_the_result_is_normalized_from_the_executor_state(slice_run):
    """The executor reports a state; RK-01 maps it. The executor never maps."""
    assert slice_run.execution["state"] == "succeeded"
    assert slice_run.execution["terminal"] is True
    assert "status" not in slice_run.execution
    assert slice_run.outcome.result["status"] == "SUCCESS"


def test_a_tool_failure_normalizes_to_error_and_still_writes_evidence(reference_kernel):
    """A failed execution is still a completed request, and still gets its record."""
    run = reference_kernel.submit(slice_request(input={"message": ""}))

    assert run.execution["state"] == "failed"
    assert run.status == "ERROR"
    assert run.outcome.result["error"]["category"] == "VALIDATION"
    assert reference_kernel.evidence.record_count == 1
    assert run.packet.evidence["provenance"]["result_status"] == "ERROR"
    assert run.packet.events[-1]["event_type"] == "runtime.execution.failed"


def test_the_result_carries_the_identity_the_contract_requires(slice_run):
    result = slice_run.outcome.result
    assert result["request_id"] == "req-slice-0001"
    assert result["correlation_id"] == "corr-slice-0001"
    assert result["execution_id"] == slice_run.execution["execution_id"]
    assert result["skill_id"] == "rk-reference-echo"
    assert result["skill_version"] == "1.0.0"


# ===========================================================================
# Determinism
# ===========================================================================


def test_two_runs_produce_identical_artifacts():
    """Every fingerprint the slice produces is a function of its inputs.

    Timestamps are injected, ids are injected, and the evidence id is derived
    rather than sampled — so re-running the certificate is a verification and not
    a second opinion.
    """
    first = build_reference_kernel().submit(slice_request())
    second = build_reference_kernel().submit(slice_request())

    assert first.artifact_fingerprints() == second.artifact_fingerprints()
    assert first.packet.evidence_id == second.packet.evidence_id
    assert dict(first.packet.evidence) == dict(second.packet.evidence)
    assert [dict(e) for e in first.packet.events] == [dict(e) for e in second.packet.events]


def test_a_different_input_changes_every_downstream_fingerprint():
    baseline = build_reference_kernel().submit(slice_request())
    changed = build_reference_kernel().submit(slice_request(input={"message": "different"}))

    a, b = baseline.artifact_fingerprints(), changed.artifact_fingerprints()
    for key in ("input_fingerprint", "route_decision", "evidence", "capability_result"):
        assert a[key] != b[key], key


def test_the_input_fingerprint_is_the_one_the_decision_and_the_evidence_share(slice_run):
    """One fingerprint, computed once, carried through."""
    assert (
        slice_run.decision["input_fingerprint"]
        == slice_run.packet.evidence["provenance"]["input_fingerprint"]
    )


# ===========================================================================
# The declared permissions, asserted by execution
# ===========================================================================


def test_the_slice_opens_no_socket():
    """``permissions.network: none``, proven rather than declared."""

    def blocked(*args, **kwargs):
        raise AssertionError("the reference slice attempted network I/O")

    real_socket, real_create = socket.socket, socket.create_connection
    socket.socket = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    try:
        run = build_reference_kernel().submit(slice_request())
    finally:
        socket.socket = real_socket  # type: ignore[assignment]
        socket.create_connection = real_create  # type: ignore[assignment]

    assert run.status == "SUCCESS"


def test_the_slice_writes_no_file():
    """``permissions.filesystem_write: false``.

    The kernel *reads* its pinned snapshot, so only write modes are counted.
    """
    real_open = builtins.open
    attempted: list[str] = []

    def guarded(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            attempted.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    builtins.open = guarded
    try:
        run = build_reference_kernel().submit(slice_request())
    finally:
        builtins.open = real_open

    assert attempted == []
    assert run.status == "SUCCESS"


def test_the_execution_envelope_carries_no_credential(slice_run, reference_kernel):
    """Empty and asserted, rather than absent and assumed."""
    from ginkgo_runtime.executor import build_execution_request

    envelope = build_execution_request(
        execution_id="exec-probe",
        request=slice_run.request,
        decision=slice_run.decision,
        input_fingerprint=slice_run.decision["input_fingerprint"],
    )
    assert envelope["credential_refs"] == []
    assert reference_kernel.contracts.runtime_execution_request.is_valid(envelope)


def test_the_execution_envelope_names_no_tool(slice_run, reference_kernel):
    """Implementation selection stays out of the execution envelope entirely.

    The tool id reaches the executor as a separate argument, from the routing
    decision — never from a field a caller or an executor could populate.
    """
    from ginkgo_runtime.executor import build_execution_request

    envelope = build_execution_request(
        execution_id="exec-probe",
        request=slice_run.request,
        decision=slice_run.decision,
        input_fingerprint=None,
    )
    # Asserted on the FIELD SET, not on the repr: the slice's own decision_id is
    # `dec-rk-reference-echo-0001`, so a substring scan would fail for a reason
    # that has nothing to do with what the envelope declares.
    assert set(envelope) == {
        "schema_version",
        "execution_id",
        "request_id",
        "correlation_id",
        "idempotency_key",
        "decision_ref",
        "attempt",
        "bounds",
        "retry_policy",
        "credential_refs",
        "input_fingerprint",
        "evidence",
    }
    assert "tool_id" not in envelope
    assert "skill_id" not in envelope
    assert "rk-echo-transform" not in repr(
        {k: v for k, v in envelope.items() if k != "decision_ref"}
    )


def test_the_executor_receives_a_reference_and_not_routing_authority(slice_run):
    """*"It may not re-resolve, substitute or override the decision."*"""
    decision_ref = slice_run.execution["decision_ref"]
    assert set(decision_ref) == {"decision_id", "decision_hash"}
    assert decision_ref["decision_hash"] == slice_run.decision["decision_hash"]


# ===========================================================================
# Adversarial-review regressions
# ===========================================================================


def test_two_distinct_requests_never_share_an_execution_id(reference_kernel):
    """Determinism must not be bought with identity.

    The ids were fixed constants, which satisfied "same inputs, same artifact"
    and quietly broke ``execution_id``: two genuinely different requests carried
    the same one, and evidence provenance records it -- so an auditor reading two
    records would have seen one execution. They are derived from the request now.
    """
    first = reference_kernel.submit(slice_request(idempotency_key="idem-A"))
    second = reference_kernel.submit(
        slice_request(request_id="req-B", idempotency_key="idem-B", input={"message": "two"})
    )

    assert first.execution["execution_id"] != second.execution["execution_id"]
    assert first.decision["decision_id"] != second.decision["decision_id"]
    assert first.packet.evidence_id != second.packet.evidence_id
    assert reference_kernel.evidence.record_count == 2


def test_the_derived_ids_are_still_deterministic():
    """The same request always produces the same ids, in any kernel."""
    first = build_reference_kernel().submit(slice_request())
    second = build_reference_kernel().submit(slice_request())

    assert first.execution["execution_id"] == second.execution["execution_id"]
    assert first.decision["decision_id"] == second.decision["decision_id"]


def test_the_same_key_in_another_workspace_is_a_separate_operation(reference_kernel):
    """The evidence ledger is scoped exactly as RK-01's is."""
    reference_kernel.submit(slice_request())
    other = reference_kernel.submit(
        slice_request(
            request_id="req-w2",
            caller={
                "actor_id": "actor-1",
                "actor_type": "API",
                "tenant_id": "tenant-1",
                "workspace_id": "workspace-2",
            },
        )
    )

    assert other.outcome.replayed is False
    assert reference_kernel.evidence.record_count == 2


def test_a_deeply_nested_input_is_refused_before_anything_runs(reference_kernel):
    deep = {"message": "hi"}
    cursor = deep
    for _ in range(3000):
        cursor["n"] = {}
        cursor = cursor["n"]

    run = reference_kernel.submit(slice_request(input=deep))

    assert run.outcome.error["code"] == "REQUEST_TOO_DEEPLY_NESTED"
    assert reference_kernel.evidence.record_count == 0
    assert reference_kernel.downstream.executions == []


def test_the_tool_mutates_none_of_its_input():
    from ginkgo_runtime.executor import echo_transform

    payload = {"message": "hi", "extra": [1, 2]}
    echo_transform(payload)
    assert payload == {"message": "hi", "extra": [1, 2]}


def test_business_input_reaches_the_output_and_never_the_evidence(reference_kernel):
    """Echoing back what was sent IS the capability, and is not a leak.

    The distinction that matters: the *caller's own* payload returns to the
    caller, while the evidence record carries a fingerprint of it and never the
    payload. A test that conflated the two would forbid the capability from
    working.
    """
    marker = "AKIA-SHAPED-BUSINESS-STRING"
    run = reference_kernel.submit(slice_request(input={"message": marker}))

    assert run.outcome.result["output"]["echoed"] == marker
    assert marker not in repr(run.packet.evidence)
    assert len(run.packet.evidence["provenance"]["input_fingerprint"]) == 64


# ===========================================================================
# The committed certificate
# ===========================================================================


def test_the_committed_certificate_matches_a_fresh_run():
    """A generated artifact is only safe to commit if CI proves it current."""
    from ginkgo_runtime.slice.__main__ import EXIT_OK, main

    assert main(["emit", "--check"]) == EXIT_OK


def test_the_certificate_promotes_nothing():
    """`ai-stack#38`: ``automatic_promotion: false``."""
    import json
    import os

    from ginkgo_runtime.slice import CERTIFICATE_ARTIFACT

    from .conftest import REPO_ROOT

    with open(os.path.join(REPO_ROOT, *CERTIFICATE_ARTIFACT.split("/")), encoding="utf-8") as fh:
        certificate = json.load(fh)

    assert certificate["promotion"]["automatic_promotion"] is False
    assert certificate["promotion"]["lifecycle_unchanged"] == "FEATURE"
    assert certificate["lifecycle"]["routing_eligible"] is False
    assert certificate["evidence"]["knowledge_promotion"]["status"] == "NOT_REQUESTED"


def test_the_certificate_records_what_it_does_not_prove():
    """Stated, so no promotion claim outruns the run."""
    import json
    import os

    from ginkgo_runtime.slice import CERTIFICATE_ARTIFACT

    from .conftest import REPO_ROOT

    with open(os.path.join(REPO_ROOT, *CERTIFICATE_ARTIFACT.split("/")), encoding="utf-8") as fh:
        certificate = json.load(fh)

    joined = " ".join(certificate["proves_nothing_about"])
    assert "provider" in joined
    assert "n8n" in joined
    assert "cross-process" in joined
    assert certificate["negative_proofs"]["network"] == "OFF"
