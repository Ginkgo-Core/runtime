"""RK-08B/A: deterministic, offline n8n executor adapter conformance."""

from __future__ import annotations

import socket
from dataclasses import FrozenInstanceError

import pytest

from ginkgo_runtime.clock import SteppingClock
from ginkgo_runtime.executor import (
    InMemoryN8nTransport,
    N8nExecutorAdapter,
    N8nTransportCancelled,
    N8nTransportError,
    N8nTransportTimeout,
    WorkflowArtifactDescriptor,
    WorkflowArtifactError,
    WorkflowArtifactPin,
    normalize_n8n_state,
)
from ginkgo_runtime.executor.contract import ExecutionEnvelopeError

CONTENT_HASH = "a" * 64
CONFIG_HASH = "b" * 64
DECISION_HASH = "c" * 64
RAW_WORKFLOW_REF = "private-n8n-workflow-7"


def workflow_artifact() -> WorkflowArtifactDescriptor:
    return WorkflowArtifactDescriptor(
        artifact_ref="workflow:rk08b/echo",
        version="1.0.0",
        transport_workflow_ref=RAW_WORKFLOW_REF,
        content_sha256=CONTENT_HASH,
        config_sha256=CONFIG_HASH,
        source_ref="Ginkgo-Core/ginkgo-runtime:tests/fixtures/rk08b-echo.json",
        source_revision="69c05066864d890c226d17d520f8278ada3fbc30",
    )


def execution_envelope(**overrides):
    envelope = {
        "schema_version": "ginkgo.runtime-execution-request.v1",
        "execution_id": "exec-rk08b-0001",
        "request_id": "req-rk08b-0001",
        "correlation_id": "corr-rk08b-0001",
        "task_id": "task-rk08b-0001",
        "idempotency_key": "idem-rk08b-0001",
        "decision_ref": {
            "decision_id": "dec-rk08b-0001",
            "decision_hash": DECISION_HASH,
        },
        "attempt": {"attempt_number": 1},
        "bounds": {
            "deadline": "2026-08-21T12:01:00+00:00",
            "timeout_ms": 5000,
            "max_cost": None,
        },
        "retry_policy": {
            "max_attempts": 1,
            "backoff": {"strategy": "none"},
            "retryable_categories": [],
        },
        "cancellation": {"cancellable": True, "cancel_token": "cancel-ref-0001"},
        "credential_refs": [],
        "input_fingerprint": "d" * 64,
        "evidence": {
            "required": True,
            "independent_validation_required": False,
        },
    }
    envelope.update(overrides)
    return envelope


def adapter_for(contracts, outcome, *, artifact=None):
    descriptor = artifact or workflow_artifact()
    transport = InMemoryN8nTransport({RAW_WORKFLOW_REF: outcome})
    adapter = N8nExecutorAdapter(
        contracts=contracts,
        clock=SteppingClock(),
        transport=transport,
        artifacts=(descriptor,),
    )
    return adapter, transport, descriptor


def execute(adapter, descriptor=None, envelope=None, payload=None):
    descriptor = descriptor or workflow_artifact()
    return adapter.execute(
        envelope or execution_envelope(),
        artifact_pin=descriptor.pin,
        payload=payload or {"message": "hello"},
    )


def test_deterministic_success_preserves_identity_pin_and_provenance(contracts):
    adapter, transport, descriptor = adapter_for(
        contracts,
        {
            "state": "success",
            "output": {"echoed": "hello"},
            "telemetry_refs": ["memory-log:exec-rk08b-0001"],
        },
    )

    result, transitions = execute(adapter, descriptor)

    assert contracts.runtime_execution_result.is_valid(result)
    assert result["state"] == "succeeded"
    assert result["terminal"] is True
    assert result["output"] == {"echoed": "hello"}
    assert result["execution_id"] == "exec-rk08b-0001"
    assert result["request_id"] == "req-rk08b-0001"
    assert result["correlation_id"] == "corr-rk08b-0001"
    assert result["task_id"] == "task-rk08b-0001"
    assert result["idempotency_key"] == "idem-rk08b-0001"
    assert result["decision_ref"] == execution_envelope()["decision_ref"]
    assert [transition.state for transition in transitions] == [
        "queued",
        "running",
        "succeeded",
    ]
    assert result["artifacts"] == [
        {
            "artifact_ref": "workflow:rk08b/echo@1.0.0",
            "sha256": CONTENT_HASH,
            "media_type": "application/vnd.n8n.workflow+json",
        },
        {
            "artifact_ref": "workflow:rk08b/echo@1.0.0#config",
            "sha256": CONFIG_HASH,
            "media_type": "application/vnd.ginkgo.executor-config+json",
        },
    ]
    assert result["evidence_telemetry_refs"][0].startswith(
        "workflow-provenance-sha256:"
    )
    assert "n8n-execution:" in result["evidence_telemetry_refs"][1]
    assert RAW_WORKFLOW_REF not in repr(result)
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    ("executor_state", "canonical_state", "category"),
    [
        ("error", "failed", "INTERNAL"),
        ("timeout", "timed_out", "TIMEOUT"),
        ("canceled", "cancelled", "CONFLICT"),
    ],
)
def test_executor_failure_timeout_and_cancellation_normalize(
    contracts, executor_state, canonical_state, category
):
    adapter, _, descriptor = adapter_for(contracts, {"state": executor_state})

    result, transitions = execute(adapter, descriptor)

    assert contracts.runtime_execution_result.is_valid(result)
    assert result["state"] == canonical_state
    assert result["error"]["category"] == category
    assert result["error"]["details"]["execution_disposition"] == "may_have_executed"
    assert transitions[-1].state == canonical_state


@pytest.mark.parametrize(
    ("failure", "state", "category"),
    [
        (N8nTransportError(), "failed", "INTERNAL"),
        (N8nTransportTimeout(), "timed_out", "TIMEOUT"),
        (N8nTransportCancelled(), "cancelled", "CONFLICT"),
    ],
)
def test_transport_failures_normalize_without_copying_upstream_text(
    contracts, failure, state, category
):
    adapter, _, descriptor = adapter_for(contracts, failure)

    result, _ = execute(adapter, descriptor)

    assert result["state"] == state
    assert result["error"]["category"] == category
    assert result["error"]["details"]["execution_disposition"] == "may_have_executed"


def test_unknown_workflow_artifact_fails_before_dispatch(contracts):
    adapter, transport, _ = adapter_for(contracts, {"state": "success"})
    unknown = WorkflowArtifactPin(
        artifact_ref="workflow:rk08b/unknown",
        version="1.0.0",
        content_sha256=CONTENT_HASH,
        config_sha256=CONFIG_HASH,
    )

    result, transitions = adapter.execute(
        execution_envelope(), artifact_pin=unknown, payload={"message": "hello"}
    )

    assert result["state"] == "failed"
    assert result["error"]["code"] == "UNKNOWN_WORKFLOW_ARTIFACT"
    assert result["error"]["details"]["execution_disposition"] == "never_started"
    assert [transition.state for transition in transitions] == ["queued", "failed"]
    assert transport.calls == []


@pytest.mark.parametrize(
    "pin",
    [
        WorkflowArtifactPin(
            artifact_ref="workflow:rk08b/echo",
            version="2.0.0",
            content_sha256=CONTENT_HASH,
            config_sha256=CONFIG_HASH,
        ),
        WorkflowArtifactPin(
            artifact_ref="workflow:rk08b/echo",
            version="1.0.0",
            content_sha256="e" * 64,
            config_sha256=CONFIG_HASH,
        ),
        WorkflowArtifactPin(
            artifact_ref="workflow:rk08b/echo",
            version="1.0.0",
            content_sha256=CONTENT_HASH,
            config_sha256="f" * 64,
        ),
    ],
)
def test_artifact_version_content_and_config_pin_mismatches_fail_closed(contracts, pin):
    adapter, transport, _ = adapter_for(contracts, {"state": "success"})

    result, _ = adapter.execute(
        execution_envelope(), artifact_pin=pin, payload={"message": "hello"}
    )

    assert result["error"]["code"] == "WORKFLOW_ARTIFACT_PIN_MISMATCH"
    assert result["error"]["category"] == "CONFLICT"
    assert result["error"]["details"]["execution_disposition"] == "never_started"
    assert transport.calls == []


@pytest.mark.parametrize(
    "outcome",
    [
        {},
        {"state": "success", "correlation_id": "wrong"},
        {"state": "success", "output": b"not-json"},
        {"state": "success", "output": {"api_key_value": "must-not-pass"}},
    ],
)
def test_malformed_executor_result_is_rejected(contracts, outcome):
    adapter, _, descriptor = adapter_for(contracts, outcome)

    result, transitions = execute(adapter, descriptor)

    assert result["state"] == "failed"
    assert result["error"]["code"] == "MALFORMED_EXECUTOR_RESULT"
    assert result["error"]["details"]["execution_disposition"] == "may_have_executed"
    assert transitions[-1].state == "failed"
    assert "must-not-pass" not in repr(result)


def test_unknown_runtime_state_is_rejected_not_promoted(contracts):
    adapter, _, descriptor = adapter_for(contracts, {"state": "completed"})

    result, _ = execute(adapter, descriptor)

    assert result["state"] == "failed"
    assert result["error"]["code"] == "MALFORMED_EXECUTOR_RESULT"
    assert "completed" not in repr(result)
    with pytest.raises(RuntimeError, match="unknown executor state"):
        normalize_n8n_state("completed")


def test_raw_credential_material_is_rejected_without_echo_or_dispatch(contracts):
    marker = "sk-raw-secret-never-print"
    adapter, transport, descriptor = adapter_for(contracts, {"state": "success"})
    envelope = execution_envelope(credential_refs=[marker])

    result, transitions = execute(adapter, descriptor, envelope=envelope)

    assert result["error"]["code"] == "RAW_CREDENTIAL_MATERIAL_REJECTED"
    assert result["error"]["details"]["execution_disposition"] == "never_started"
    assert marker not in repr(result)
    assert marker not in repr(transitions)
    assert transport.calls == []


def test_opaque_credential_handles_and_bounds_are_propagated_only(contracts):
    adapter, transport, descriptor = adapter_for(contracts, {"state": "success"})
    envelope = execution_envelope(credential_refs=["credref:tenant-1/n8n/mail"])

    execute(adapter, descriptor, envelope=envelope)

    call = transport.calls[0]
    assert call.credential_refs == ("credref:tenant-1/n8n/mail",)
    assert call.timeout_ms == 5000
    assert call.deadline == "2026-08-21T12:01:00+00:00"
    assert call.cancel_token == "cancel-ref-0001"
    assert call.attempt_number == 1
    assert call.task_id == "task-rk08b-0001"


@pytest.mark.parametrize("field", ["capability", "provider", "model"])
def test_execution_envelope_cannot_smuggle_selection_authority(contracts, field):
    adapter, transport, descriptor = adapter_for(contracts, {"state": "success"})
    envelope = execution_envelope()
    envelope[field] = "caller-selection"

    with pytest.raises(ExecutionEnvelopeError):
        execute(adapter, descriptor, envelope=envelope)

    assert transport.calls == []


def test_transport_shape_contains_no_capability_provider_model_or_fallback_authority(contracts):
    adapter, transport, descriptor = adapter_for(
        contracts, {"state": "success", "output": {"ok": True}}
    )
    payload = {"provider": "opaque business data", "model": "opaque business data"}

    execute(adapter, descriptor, payload=payload)

    call = transport.calls[0]
    assert call.payload == payload
    fields = set(vars(call))
    assert not fields.intersection(
        {"capability", "skill", "binding", "provider", "model", "fallback"}
    )


def test_correlation_and_idempotency_echo_mismatch_is_malformed(contracts):
    for field in ("execution_id", "request_id", "correlation_id", "idempotency_key"):
        adapter, _, descriptor = adapter_for(
            contracts, {"state": "success", field: "mismatch"}
        )
        result, _ = execute(adapter, descriptor)
        assert result["error"]["code"] == "MALFORMED_EXECUTOR_RESULT", field


def test_independent_validation_requirement_is_propagated_not_self_satisfied(contracts):
    adapter, _, descriptor = adapter_for(contracts, {"state": "success"})
    envelope = execution_envelope(
        evidence={"required": True, "independent_validation_required": True}
    )

    result, _ = execute(adapter, descriptor, envelope=envelope)

    assert result["state"] == "succeeded"
    assert result["independent_validation"] == {"required": True, "satisfied": None}


def test_same_deterministic_input_produces_equivalent_normalized_output(contracts):
    outcome = {
        "state": "success",
        "output": {"echoed": "hello"},
        "telemetry_refs": ["memory-log:exec-rk08b-0001"],
    }
    first, _, first_descriptor = adapter_for(contracts, outcome)
    second, _, second_descriptor = adapter_for(contracts, outcome)

    assert execute(first, first_descriptor) == execute(second, second_descriptor)


def test_artifact_descriptor_and_pin_are_immutable_and_invalid_refs_fail_closed():
    descriptor = workflow_artifact()
    with pytest.raises(FrozenInstanceError):
        descriptor.version = "2.0.0"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        descriptor.pin.version = "2.0.0"  # type: ignore[misc]
    with pytest.raises(WorkflowArtifactError, match="artifact_ref"):
        WorkflowArtifactPin(
            artifact_ref="raw workflow id with spaces",
            version="1.0.0",
            content_sha256=CONTENT_HASH,
        )


def test_raw_n8n_workflow_id_never_reaches_public_capability_contract(contracts):
    public_request = {
        "request_id": "r",
        "correlation_id": "c",
        "caller": {
            "actor_id": "a",
            "actor_type": "API",
            "tenant_id": "t",
            "workspace_id": "w",
        },
        "capability": {"name": "system.echo.execute", "contract_version": "v1"},
        "input": {},
        "constraints": {"fallback_policy": "none"},
        "governance": {"approval_context": {}, "external_gate_context": {}},
        "evidence": {"required": True},
        "idempotency_key": "i",
        "workflow_id": RAW_WORKFLOW_REF,
    }

    assert not contracts.capability_request.is_valid(public_request)
    assert RAW_WORKFLOW_REF not in repr(workflow_artifact())


def test_malformed_runtime_execution_request_raises_before_result_or_dispatch(contracts):
    adapter, transport, descriptor = adapter_for(contracts, {"state": "success"})
    envelope = execution_envelope()
    del envelope["correlation_id"]

    with pytest.raises(ExecutionEnvelopeError, match="RuntimeExecutionRequest"):
        execute(adapter, descriptor, envelope=envelope)

    assert transport.calls == []


def test_generic_transport_exception_never_leaks_its_message(contracts):
    marker = "raw-upstream-secret-body"
    adapter, _, descriptor = adapter_for(contracts, RuntimeError(marker))

    result, transitions = execute(adapter, descriptor)

    assert result["error"]["code"] == "N8N_TRANSPORT_ERROR"
    assert marker not in repr(result)
    assert marker not in repr(transitions)


def test_offline_adapter_opens_no_socket(contracts, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("n8n adapter attempted network I/O")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    adapter, _, descriptor = adapter_for(contracts, {"state": "success"})

    result, _ = execute(adapter, descriptor)

    assert result["state"] == "succeeded"
